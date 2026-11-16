"""Natural-language Q&A agent over routing decisions (OpenAI tool calling, bounded tool calls)."""
import json
import logging

from sqlalchemy import select

from . import runs
from .ai import client
from .config import OPENAI_MODEL
from .db import SessionLocal
from .learning import current_weights, outcome_stats
from .models import Assignment, AuditEvent, LoanApplication, RoutingWeights, Underwriter

log = logging.getLogger(__name__)

ASK_STEPS = ["gather_context", "agent_reasoning", "compose_answer"]
ASK_TOOL_LIMITS = {"get_decision": 2, "get_underwriter": 3, "get_audit_trail": 2, "get_learning": 1}
MAX_TURNS = 4

SYSTEM = """You answer questions about why a loan application was routed to a particular underwriter.
Use the tools to fetch facts. Answer ONLY from tool results; if the data does not answer the question, say so.
Refer to underwriters by name. For questions about learning or weight changes, use get_learning.
Be concise (2-5 sentences) and cite concrete numbers (scores, caseload, SLA risk, exclusion reasons)."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_decision",
        "description": "Routing decision for the application: chosen underwriter, rationale, candidate scores, "
                       "AI adjustments, excluded underwriters with reasons, overrides.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "get_underwriter",
        "description": "Profile and current caseload of an underwriter, looked up by name (partial ok).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "get_audit_trail",
        "description": "Chronological audit events for the application.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "get_learning",
        "description": "Adaptive learning state: current scoring weights and version, how they changed and why "
                       "(from outcomes), recent SLA-miss and rework rates.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]


def _decision(db, app_id: int) -> dict:
    rows = db.execute(select(Assignment, Underwriter.name).join(Underwriter)
                      .where(Assignment.application_id == app_id).order_by(Assignment.id)).all()
    if not rows:
        app = db.get(LoanApplication, app_id)
        return {"status": app.status if app else "unknown", "assignments": []}
    active = next(((a, n) for a, n in rows if a.is_active), rows[-1])
    a, name = active
    return {
        "chosen_underwriter": name, "method": a.method, "rationale": a.rationale,
        "override_reason": a.override_reason,
        "candidates": a.decision.get("candidates"), "excluded": a.decision.get("excluded"),
        "used_fallback": a.decision.get("used_fallback"),
        "weights_version": a.decision.get("weights_version"), "weights": a.decision.get("weights"),
        "history": [{"underwriter": n, "method": x.method, "active": x.is_active,
                     "override_reason": x.override_reason} for x, n in rows],
    }


def _underwriter(db, name: str) -> dict:
    uw = db.execute(select(Underwriter).where(Underwriter.name.ilike(f"%{name}%"))).scalars().first()
    if not uw:
        return {"error": f"No underwriter matching '{name}'"}
    load = db.query(Assignment).filter(Assignment.underwriter_id == uw.id, Assignment.is_active).count()
    return {"name": uw.name, "loan_types": uw.loan_types, "regions": uw.regions, "max_amount": uw.max_amount,
            "load": load, "max_caseload": uw.max_caseload, "avg_turnaround_hours": uw.avg_turnaround_hours,
            "quality_score": uw.quality_score, "active": uw.active, "profile": uw.profile}


def _audit(db, app_id: int) -> list[dict]:
    evs = db.execute(select(AuditEvent).where(AuditEvent.application_id == app_id)
                     .order_by(AuditEvent.id)).scalars().all()
    return [{"event": e.event_type, "actor": e.actor, "at": e.created_at.isoformat(),
             "reason": e.payload.get("reason")} for e in evs]


def _run_tool(db, app_id: int, name: str, args: dict):
    if name == "get_decision":
        return _decision(db, app_id)
    if name == "get_underwriter":
        return _underwriter(db, str(args.get("name", "")))
    if name == "get_audit_trail":
        return _audit(db, app_id)
    if name == "get_learning":
        version, weights = current_weights(db)
        hist = db.execute(select(RoutingWeights).order_by(RoutingWeights.version.desc()).limit(5)).scalars().all()
        return {"current_version": version, "current_weights": weights, "stats": outcome_stats(db),
                "history": [{"version": h.version, "weights": h.weights, "reason": h.reason} for h in hist]}
    return {"error": f"Unknown tool {name}"}


def _fallback_answer(db, run_id: str, app_id: int, question: str) -> str:
    runs.tool_call(run_id, "get_decision")
    d = _decision(db, app_id)
    runs.tool_done(run_id, "get_decision")
    if not d.get("chosen_underwriter"):
        return f"Application is {d.get('status')}; no underwriter has been assigned yet."
    parts = [f"Assigned to {d['chosen_underwriter']} ({d['method']}). {d['rationale']}"]
    q = question.lower()
    for c in d.get("candidates") or []:
        if c["name"].split()[0].lower() in q and c["name"] != d["chosen_underwriter"]:
            parts.append(f"{c['name']} scored {c['final_score']:.3f} (components {c['components']}).")
    for e in d.get("excluded") or []:
        if e["name"].split()[0].lower() in q:
            parts.append(f"{e['name']} was excluded: {'; '.join(e['reasons'])}.")
    return " ".join(parts)


def execute_ask(application_id: int, question: str, run_id: str) -> None:
    db = SessionLocal()
    try:
        runs.set_step(run_id, "gather_context", "running")
        if not db.get(LoanApplication, application_id):
            raise ValueError("Application not found")
        runs.set_step(run_id, "gather_context", "done")

        runs.set_step(run_id, "agent_reasoning", "running")
        if client is None:
            answer = _fallback_answer(db, run_id, application_id, question)
        else:
            messages: list[dict] = [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Application #{application_id}. Question: {question}"},
            ]
            answer = None
            for _ in range(MAX_TURNS):
                resp = client.chat.completions.create(model=OPENAI_MODEL, messages=messages, tools=TOOLS)
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    answer = msg.content
                    break
                messages.append({"role": "assistant", "content": msg.content,
                                 "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if name not in ASK_TOOL_LIMITS:
                        result = {"error": f"Unknown tool {name}"}
                    elif not runs.tool_call(run_id, name):
                        result = {"error": "Tool call limit reached; answer with what you have."}
                    else:
                        result = _run_tool(db, application_id, name, args)
                        runs.tool_done(run_id, name)
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(result, default=str)})
            if answer is None:
                resp = client.chat.completions.create(model=OPENAI_MODEL, messages=messages)
                answer = resp.choices[0].message.content
        runs.set_step(run_id, "agent_reasoning", "done")

        runs.set_step(run_id, "compose_answer", "running")
        runs.set_step(run_id, "compose_answer", "done")
        runs.finish(run_id, "succeeded", result={"answer": answer or "No answer produced."})
    except Exception as e:
        log.exception("ask failed")
        runs.finish(run_id, "failed", error=str(e))
    finally:
        db.close()
