"""LangGraph routing pipeline: load -> eligible -> score -> genai -> guard -> assign (or queue)."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import func, select

from . import ai, runs
from .config import OPENAI_MODEL
from .learning import current_weights
from .models import Assignment, AuditEvent, LoanApplication, Notification, Underwriter
from .rules import eligibility_reasons, humanize_ids, score, template_rationale

log = logging.getLogger(__name__)

ROUTE_STEPS = ["load_application", "find_eligible", "score_rules", "genai_optimize", "guard",
               "assign_and_notify", "queue"]
ROUTE_TOOL_LIMITS = {"openai.optimize": 1}
SHORTLIST = 3
MAX_DELTA = 0.10


class RouteState(TypedDict, total=False):
    db: Any
    run_id: str
    actor: str
    application_id: int
    application: Any
    existing_assignment_id: int | None
    underwriters: dict
    eligible: list[int]
    excluded: list[dict]
    scored: list[dict]
    ai_output: dict | None
    used_fallback: bool
    fallback_reason: str | None
    final: list[dict]
    chosen: dict
    rationale: str
    assignment_id: int
    weights_version: int
    weights: dict


def step(name):
    def wrap(fn):
        def inner(state: RouteState):
            runs.set_step(state["run_id"], name, "running")
            out = fn(state)
            runs.set_step(state["run_id"], name, "done")
            return out
        return inner
    return wrap


def app_dict(app: LoanApplication) -> dict:
    return {"id": app.id, "loan_type": app.loan_type, "amount": app.amount, "region": app.region,
            "credit_score": app.credit_score, "priority": app.priority, "notes": app.notes,
            "sla_due_at": app.sla_due_at.isoformat()}


def _active_loads(db) -> dict[int, int]:
    return dict(db.execute(select(Assignment.underwriter_id, func.count())
                           .where(Assignment.is_active).group_by(Assignment.underwriter_id)).all())


@step("load_application")
def load_application(state: RouteState):
    db = state["db"]
    app = db.execute(select(LoanApplication).where(LoanApplication.id == state["application_id"])
                     .with_for_update()).scalar_one()
    existing = db.execute(select(Assignment.id).where(Assignment.application_id == app.id,
                                                      Assignment.is_active)).scalar_one_or_none()
    return {"application": app, "existing_assignment_id": existing}


@step("find_eligible")
def find_eligible(state: RouteState):
    db, app = state["db"], state["application"]
    uws = db.execute(select(Underwriter).order_by(Underwriter.id)).scalars().all()
    loads = _active_loads(db)
    eligible, excluded = [], []
    for uw in uws:
        reasons = eligibility_reasons(uw, app, loads.get(uw.id, 0))
        if reasons:
            excluded.append({"underwriter_id": uw.id, "name": uw.name, "reasons": reasons})
        else:
            eligible.append(uw.id)
    return {"underwriters": {u.id: u for u in uws}, "eligible": eligible, "excluded": excluded}


@step("score_rules")
def score_rules(state: RouteState):
    db, app = state["db"], state["application"]
    loads = _active_loads(db)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = dict(db.execute(select(Assignment.underwriter_id, func.count())
                             .where(Assignment.created_at >= since).group_by(Assignment.underwriter_id)).all())
    avg_recent = sum(recent.get(i, 0) for i in state["eligible"]) / len(state["eligible"])
    version, weights = current_weights(db)  # adaptive: latest learned weights
    scored = [score(state["underwriters"][i], app, loads.get(i, 0), recent.get(i, 0), avg_recent, weights)
              for i in state["eligible"]]
    scored.sort(key=lambda c: (-c["rules_score"], c["underwriter_id"]))  # deterministic tie-break
    return {"scored": scored, "weights_version": version, "weights": weights}


@step("genai_optimize")
def genai_optimize(state: RouteState):
    run_id, app = state["run_id"], state["application"]
    shortlist = []
    for c in state["scored"][:SHORTLIST]:
        uw = state["underwriters"][c["underwriter_id"]]
        shortlist.append({**c, "loan_types": uw.loan_types, "regions": uw.regions,
                          "avg_turnaround_hours": uw.avg_turnaround_hours,
                          "quality_score": uw.quality_score, "profile": uw.profile})
    if len(shortlist) == 1:
        return {"ai_output": None, "used_fallback": True, "fallback_reason": "Only one eligible candidate"}
    if not runs.tool_call(run_id, "openai.optimize"):
        return {"ai_output": None, "used_fallback": True, "fallback_reason": "Tool call limit reached"}
    try:
        out = ai.optimize(app_dict(app), shortlist)
        runs.tool_done(run_id, "openai.optimize", True)
        return {"ai_output": out, "used_fallback": False, "fallback_reason": None}
    except Exception as e:  # never block routing on the LLM
        log.warning("GenAI optimize failed: %s", e)
        runs.tool_done(run_id, "openai.optimize", False)
        return {"ai_output": None, "used_fallback": True, "fallback_reason": f"LLM unavailable: {type(e).__name__}"}


@step("guard")
def guard(state: RouteState):
    shortlist = state["scored"][:SHORTLIST]
    ids = {c["underwriter_id"] for c in shortlist}
    adjustments: dict[int, tuple[float, str]] = {}
    ai_out = state.get("ai_output") or {}
    for a in ai_out.get("adjustments", []) or []:
        try:
            uid = int(a["underwriter_id"])
            if uid in ids:
                delta = max(-MAX_DELTA, min(MAX_DELTA, float(a.get("delta", 0))))
                adjustments[uid] = (delta, str(a.get("reason", "")))
        except (KeyError, TypeError, ValueError):
            continue
    final = []
    for c in shortlist:
        delta, reason = adjustments.get(c["underwriter_id"], (0.0, ""))
        final.append({**c, "ai_delta": round(delta, 3), "ai_reason": reason,
                      "final_score": round(c["rules_score"] + delta, 4)})
    final.sort(key=lambda c: (-c["final_score"], c["underwriter_id"]))
    chosen = final[0]
    runner_up = final[1] if len(final) > 1 else None
    llm_rationale = ai_out.get("rationale")
    try:
        llm_choice = int(ai_out.get("chosen_underwriter_id"))
    except (TypeError, ValueError):
        llm_choice = None
    if llm_rationale and llm_choice == chosen["underwriter_id"]:
        rationale = humanize_ids(str(llm_rationale), {c["underwriter_id"]: c["name"] for c in state["scored"]})
    else:
        rationale = template_rationale(state["application"], chosen, runner_up, len(state["excluded"]))
        if runner_up is None:
            # State the actual basis of selection when there was no one to compare against.
            app = state["application"]
            rationale += (f" {chosen['name']} is the only underwriter who meets every eligibility rule for this "
                          f"application: {app.loan_type} expertise, licensed for {app.region}, approval authority of "
                          f"at least ${app.amount:,.0f}, active, and with free capacity.")
    return {"final": final, "chosen": chosen, "rationale": rationale}


@step("assign_and_notify")
def assign_and_notify(state: RouteState):
    db, app, chosen = state["db"], state["application"], state["chosen"]
    uw = state["underwriters"][chosen["underwriter_id"]]
    decision = {
        "candidates": state["final"], "all_scored": state["scored"], "excluded": state["excluded"],
        "ai_output": state.get("ai_output"), "used_fallback": state.get("used_fallback", True),
        "fallback_reason": state.get("fallback_reason"), "model": OPENAI_MODEL,
        "weights_version": state.get("weights_version", 0), "weights": state.get("weights"),
    }
    asg = Assignment(application_id=app.id, underwriter_id=uw.id, method="AUTO", is_active=True,
                     rationale=state["rationale"], decision=decision, created_by=state["actor"])
    db.add(asg)
    app.status = "ROUTED"
    db.flush()
    db.add(AuditEvent(application_id=app.id, event_type="ROUTED", actor=state["actor"],
                      payload={"assignment_id": asg.id, "underwriter_id": uw.id, **decision}))
    db.add(Notification(recipient=uw.email, application_id=app.id,
                        message=f"New {app.loan_type} application #{app.id} (${app.amount:,.0f}) assigned to you."))
    db.add(Notification(recipient=app.submitter, application_id=app.id,
                        message=f"Application #{app.id} assigned to {uw.name}. Rationale: {state['rationale']}"))
    return {"assignment_id": asg.id}


@step("queue")
def queue(state: RouteState):
    db, app = state["db"], state["application"]
    app.status = "QUEUED"
    db.add(AuditEvent(application_id=app.id, event_type="QUEUED", actor=state["actor"],
                      payload={"excluded": state["excluded"]}))
    db.add(Notification(recipient=app.submitter, application_id=app.id,
                        message=f"Application #{app.id} queued: no eligible underwriter right now."))
    return {}


def _build():
    g = StateGraph(RouteState)
    for name, fn in [("load_application", load_application), ("find_eligible", find_eligible),
                     ("score_rules", score_rules), ("genai_optimize", genai_optimize), ("guard", guard),
                     ("assign_and_notify", assign_and_notify), ("queue", queue)]:
        g.add_node(name, fn)
    g.set_entry_point("load_application")
    g.add_conditional_edges("load_application",
                            lambda s: "done" if s.get("existing_assignment_id") else "continue",
                            {"done": END, "continue": "find_eligible"})
    g.add_conditional_edges("find_eligible", lambda s: "score" if s["eligible"] else "queue",
                            {"score": "score_rules", "queue": "queue"})
    g.add_edge("score_rules", "genai_optimize")
    g.add_edge("genai_optimize", "guard")
    g.add_edge("guard", "assign_and_notify")
    g.add_edge("assign_and_notify", END)
    g.add_edge("queue", END)
    return g.compile()


ROUTE_GRAPH = _build()
