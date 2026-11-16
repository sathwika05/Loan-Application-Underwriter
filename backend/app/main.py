import logging
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import runs
from .ask import ASK_STEPS, ASK_TOOL_LIMITS, execute_ask
from .db import Base, SessionLocal, engine, get_db
from .graph import ROUTE_GRAPH, ROUTE_STEPS, ROUTE_TOOL_LIMITS
from .learning import current_weights, learn_from_outcome, outcome_stats
from .models import Assignment, AuditEvent, LoanApplication, Notification, Outcome, RoutingWeights, Underwriter
from .rules import eligibility_reasons, sla_due
from .seed import seed

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

_latest_run: dict[int, str] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    # create_all does not alter existing tables: widen the status check for COMPLETED (idempotent).
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE loan_application DROP CONSTRAINT IF EXISTS ck_app_status"))
        conn.execute(text("ALTER TABLE loan_application ADD CONSTRAINT ck_app_status "
                          "CHECK (status IN ('NEW','ROUTED','QUEUED','COMPLETED'))"))
    with SessionLocal() as db:
        seed(db)
        # Recovery: routing runs in a background thread, so a restart mid-run can strand an application.
        # Re-route anything still NEW or QUEUED; the graph's lock + active-assignment check make this safe.
        pending = db.execute(select(LoanApplication.id).where(LoanApplication.status.in_(("NEW", "QUEUED")))
                             .order_by(LoanApplication.id)).scalars().all()
    for app_id in pending:
        start_route(app_id, "startup-recovery", "startup")
    if pending:
        log.info("Startup recovery: re-routing %d pending application(s): %s", len(pending), pending)
    yield


app = FastAPI(title="Loan Underwriter Routing", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Request-ID"])


# ---------- request id + uniform errors ----------
@app.middleware("http")
async def request_id_mw(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "request_id": _rid(request)},
                        headers={"X-Request-ID": _rid(request)})


@app.exception_handler(RequestValidationError)
async def validation_exc(request: Request, exc: RequestValidationError):
    msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return JSONResponse(status_code=422, content={"detail": msg, "request_id": _rid(request)},
                        headers={"X-Request-ID": _rid(request)})


@app.exception_handler(Exception)
async def unhandled_exc(request: Request, exc: Exception):
    log.exception("Unhandled error [%s]", _rid(request))
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": _rid(request)})


# ---------- schemas ----------
class ApplicationIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=64)
    applicant_name: str = Field(min_length=1, max_length=100)
    submitter: str = Field(min_length=1, max_length=100)
    loan_type: Literal["mortgage", "auto", "personal", "business"]
    amount: float = Field(gt=0)
    region: Literal["East", "West", "Midwest", "South"]
    credit_score: int = Field(ge=300, le=850)
    priority: Literal["normal", "high", "urgent"] = "normal"
    notes: str | None = None


class OverrideIn(BaseModel):
    underwriter_id: int
    reason: str = Field(min_length=10, max_length=1000)
    actor: str = Field(min_length=1, max_length=100)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class CompleteIn(BaseModel):
    decision: Literal["approved", "declined"]
    turnaround_hours: float = Field(gt=0, le=2000)
    rework: bool = False
    actor: str = Field(min_length=1, max_length=100)


# ---------- helpers ----------
def iso(dt):
    return dt.isoformat() if dt else None


def app_out(a: LoanApplication) -> dict:
    return {"id": a.id, "applicant_name": a.applicant_name, "submitter": a.submitter, "loan_type": a.loan_type,
            "amount": a.amount, "region": a.region, "credit_score": a.credit_score, "priority": a.priority,
            "notes": a.notes, "status": a.status, "sla_due_at": iso(a.sla_due_at), "created_at": iso(a.created_at)}


def assignment_out(a: Assignment, uw_name: str) -> dict:
    return {"id": a.id, "underwriter_id": a.underwriter_id, "underwriter_name": uw_name, "method": a.method,
            "is_active": a.is_active, "rationale": a.rationale, "decision": a.decision,
            "override_reason": a.override_reason, "created_by": a.created_by, "created_at": iso(a.created_at)}


def outcome_out(o: Outcome) -> dict:
    return {"id": o.id, "decision": o.decision, "turnaround_hours": o.turnaround_hours, "sla_met": o.sla_met,
            "rework": o.rework, "recorded_by": o.recorded_by, "created_at": iso(o.created_at)}


def run_route(application_id: int, run_id: str, actor: str) -> None:
    db = SessionLocal()
    try:
        out = ROUTE_GRAPH.invoke({"db": db, "run_id": run_id, "actor": actor, "application_id": application_id})
        db.commit()
        if out.get("existing_assignment_id"):
            result = {"outcome": "ALREADY_ROUTED", "assignment_id": out["existing_assignment_id"]}
        elif out.get("assignment_id"):
            result = {"outcome": "ROUTED", "assignment_id": out["assignment_id"],
                      "underwriter_name": out["chosen"]["name"], "rationale": out["rationale"],
                      "used_fallback": out.get("used_fallback", True)}
        else:
            result = {"outcome": "QUEUED", "excluded": out.get("excluded", [])}
        runs.finish(run_id, "succeeded", result={"application_id": application_id, **result})
    except IntegrityError:
        db.rollback()  # unique active-assignment index blocked a duplicate routing
        runs.finish(run_id, "succeeded", result={"application_id": application_id, "outcome": "ALREADY_ROUTED"})
    except Exception as e:
        db.rollback()
        log.exception("routing failed")
        runs.finish(run_id, "failed", error=str(e))
    finally:
        db.close()


def start_route(application_id: int, actor: str, request_id: str) -> dict:
    run = runs.create("route", ROUTE_STEPS, ROUTE_TOOL_LIMITS, application_id, request_id)
    _latest_run[application_id] = run["run_id"]
    threading.Thread(target=run_route, args=(application_id, run["run_id"], actor), daemon=True).start()
    return run


# ---------- endpoints ----------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/applications", status_code=202)
def create_application(body: ApplicationIn, request: Request, db: Session = Depends(get_db)):
    existing = db.execute(select(LoanApplication)
                          .where(LoanApplication.idempotency_key == body.idempotency_key)).scalar_one_or_none()
    if existing is None:
        a = LoanApplication(**body.model_dump(), sla_due_at=sla_due(body.priority), status="NEW")
        db.add(a)
        try:
            db.flush()
            db.add(AuditEvent(application_id=a.id, event_type="SUBMITTED", actor=body.submitter,
                              payload=body.model_dump()))
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(select(LoanApplication)
                                  .where(LoanApplication.idempotency_key == body.idempotency_key)).scalar_one()
    if existing is not None:  # duplicate submit: return the original, never route twice
        return {"application_id": existing.id, "run_id": _latest_run.get(existing.id), "duplicate": True,
                "request_id": _rid(request)}
    run = start_route(a.id, "system", _rid(request))
    return {"application_id": a.id, "run_id": run["run_id"], "duplicate": False, "request_id": _rid(request)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found (the server may have restarted)")
    return run


@app.get("/api/applications")
def list_applications(status: str | None = None, db: Session = Depends(get_db)):
    # Active assignment for open cases; for COMPLETED cases (assignment deactivated) use whoever worked it.
    q = (select(LoanApplication, Underwriter.name)
         .outerjoin(Assignment, (Assignment.application_id == LoanApplication.id) & Assignment.is_active)
         .outerjoin(Outcome, Outcome.application_id == LoanApplication.id)
         .outerjoin(Underwriter, Underwriter.id == func.coalesce(Assignment.underwriter_id, Outcome.underwriter_id))
         .order_by(LoanApplication.id.desc()))
    if status:
        q = q.where(LoanApplication.status == status)
    return [{**app_out(a), "underwriter_name": n} for a, n in db.execute(q).all()]


@app.get("/api/applications/{app_id}")
def get_application(app_id: int, db: Session = Depends(get_db)):
    a = db.get(LoanApplication, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    rows = db.execute(select(Assignment, Underwriter.name).join(Underwriter)
                      .where(Assignment.application_id == app_id).order_by(Assignment.id)).all()
    history = [assignment_out(x, n) for x, n in rows]
    audit = db.execute(select(AuditEvent).where(AuditEvent.application_id == app_id)
                       .order_by(AuditEvent.id)).scalars().all()
    notes = db.execute(select(Notification).where(Notification.application_id == app_id)
                       .order_by(Notification.id)).scalars().all()
    out = db.execute(select(Outcome).where(Outcome.application_id == app_id)
                     .order_by(Outcome.id.desc()).limit(1)).scalar_one_or_none()
    current = next((h for h in history if h["is_active"]), None)
    if current is None and a.status == "COMPLETED" and history:
        current = history[-1]  # completed: show who worked it
    return {
        "application": app_out(a),
        "active_assignment": current,
        "outcome": outcome_out(out) if out else None,
        "history": history,
        "audit": [{"id": e.id, "event_type": e.event_type, "actor": e.actor, "payload": e.payload,
                   "created_at": iso(e.created_at)} for e in audit],
        "notifications": [{"recipient": n.recipient, "message": n.message, "created_at": iso(n.created_at)}
                          for n in notes],
        "latest_run_id": _latest_run.get(app_id),
    }


@app.post("/api/applications/{app_id}/route", status_code=202)
def reroute(app_id: int, request: Request, db: Session = Depends(get_db)):
    a = db.get(LoanApplication, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    if a.status == "ROUTED":
        raise HTTPException(409, "Application is already routed; use override to reassign")
    run = start_route(app_id, "manual-reroute", _rid(request))
    return {"application_id": app_id, "run_id": run["run_id"], "request_id": _rid(request)}


@app.post("/api/applications/{app_id}/override")
def override(app_id: int, body: OverrideIn, request: Request, db: Session = Depends(get_db)):
    a = db.execute(select(LoanApplication).where(LoanApplication.id == app_id).with_for_update()).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Application not found")
    if a.status == "COMPLETED":
        raise HTTPException(409, "Application is already completed; it cannot be reassigned")
    uw = db.get(Underwriter, body.underwriter_id)
    if not uw or not uw.active:
        raise HTTPException(400, "Target underwriter does not exist or is inactive")
    current = db.execute(select(Assignment).where(Assignment.application_id == app_id, Assignment.is_active)
                         ).scalar_one_or_none()
    if current and current.underwriter_id == uw.id:
        raise HTTPException(400, "Application is already assigned to this underwriter")
    load = db.query(Assignment).filter(Assignment.underwriter_id == uw.id, Assignment.is_active).count()
    warnings = eligibility_reasons(uw, a, load)
    if current:
        current.is_active = False
        db.flush()
    new = Assignment(application_id=app_id, underwriter_id=uw.id, method="OVERRIDE", is_active=True,
                     rationale=f"Manual override by {body.actor}: {body.reason}", override_reason=body.reason,
                     decision={"previous_assignment_id": current.id if current else None,
                               "eligibility_warnings": warnings},
                     created_by=body.actor)
    db.add(new)
    a.status = "ROUTED"
    db.flush()
    db.add(AuditEvent(application_id=app_id, event_type="OVERRIDE", actor=body.actor,
                      payload={"from_underwriter_id": current.underwriter_id if current else None,
                               "to_underwriter_id": uw.id, "reason": body.reason, "warnings": warnings}))
    db.add(Notification(recipient=uw.email, application_id=app_id,
                        message=f"Application #{app_id} reassigned to you by {body.actor}."))
    db.add(Notification(recipient=a.submitter, application_id=app_id,
                        message=f"Application #{app_id} reassigned to {uw.name}. Reason: {body.reason}"))
    db.commit()
    return {"assignment": assignment_out(new, uw.name), "warnings": warnings, "request_id": _rid(request)}


@app.post("/api/applications/{app_id}/ask", status_code=202)
def ask(app_id: int, body: AskIn, request: Request, db: Session = Depends(get_db)):
    if not db.get(LoanApplication, app_id):
        raise HTTPException(404, "Application not found")
    run = runs.create("ask", ASK_STEPS, ASK_TOOL_LIMITS, app_id, _rid(request))
    threading.Thread(target=execute_ask, args=(app_id, body.question, run["run_id"]), daemon=True).start()
    return {"application_id": app_id, "run_id": run["run_id"], "request_id": _rid(request)}


@app.post("/api/applications/{app_id}/complete")
def complete(app_id: int, body: CompleteIn, request: Request, db: Session = Depends(get_db)):
    """Record the outcome of a routed case, learn from it, then re-route anything waiting for capacity."""
    a = db.execute(select(LoanApplication).where(LoanApplication.id == app_id).with_for_update()).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Application not found")
    if a.status != "ROUTED":
        raise HTTPException(409, f"Only ROUTED applications can be completed (status is {a.status})")
    asg = db.execute(select(Assignment).where(Assignment.application_id == app_id, Assignment.is_active)
                     ).scalar_one_or_none()
    if not asg:
        raise HTTPException(409, "No active assignment to complete")
    uw = db.get(Underwriter, asg.underwriter_id)
    sla_met = asg.created_at + timedelta(hours=body.turnaround_hours) <= a.sla_due_at
    outcome = Outcome(assignment_id=asg.id, application_id=app_id, underwriter_id=uw.id, decision=body.decision,
                      turnaround_hours=body.turnaround_hours, sla_met=sla_met, rework=body.rework,
                      recorded_by=body.actor)
    db.add(outcome)
    asg.is_active = False  # frees the underwriter's capacity
    a.status = "COMPLETED"
    db.flush()
    db.add(AuditEvent(application_id=app_id, event_type="COMPLETED", actor=body.actor, payload=outcome_out(outcome)))
    learned = learn_from_outcome(db, uw, outcome, body.actor)
    db.add(Notification(recipient=a.submitter, application_id=app_id,
                        message=f"Application #{app_id} {body.decision} by {uw.name}."))
    db.commit()
    rerouted = []
    for qid in db.execute(select(LoanApplication.id).where(LoanApplication.status == "QUEUED")
                          .order_by(LoanApplication.id)).scalars().all():
        rerouted.append({"application_id": qid,
                         "run_id": start_route(qid, "capacity-freed", _rid(request))["run_id"]})
    return {"outcome": outcome_out(outcome), "learning": learned, "rerouted_queued": rerouted,
            "request_id": _rid(request)}


@app.get("/api/learning")
def learning(db: Session = Depends(get_db)):
    version, weights = current_weights(db)
    history = db.execute(select(RoutingWeights).order_by(RoutingWeights.version.desc()).limit(10)).scalars().all()
    return {"weights_version": version, "weights": weights, "stats": outcome_stats(db),
            "history": [{"version": h.version, "weights": h.weights, "reason": h.reason,
                         "created_at": iso(h.created_at)} for h in history]}


@app.get("/api/underwriters")
def underwriters(db: Session = Depends(get_db)):
    loads = dict(db.execute(select(Assignment.underwriter_id, func.count()).where(Assignment.is_active)
                            .group_by(Assignment.underwriter_id)).all())
    done = dict(db.execute(select(Outcome.underwriter_id, func.count()).group_by(Outcome.underwriter_id)).all())
    uws = db.execute(select(Underwriter).order_by(Underwriter.id)).scalars().all()
    return [{"id": u.id, "name": u.name, "email": u.email, "loan_types": u.loan_types, "regions": u.regions,
             "max_amount": u.max_amount, "load": loads.get(u.id, 0), "max_caseload": u.max_caseload,
             "quality_score": u.quality_score, "avg_turnaround_hours": u.avg_turnaround_hours,
             "completed": done.get(u.id, 0), "active": u.active} for u in uws]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(LoanApplication.status, func.count()).group_by(LoanApplication.status)).all())
    recent = db.execute(select(Assignment, Underwriter.name, LoanApplication.applicant_name)
                        .join(Underwriter, Underwriter.id == Assignment.underwriter_id)
                        .join(LoanApplication, LoanApplication.id == Assignment.application_id)
                        .order_by(Assignment.id.desc()).limit(10)).all()
    completed_ids = set(db.execute(select(Outcome.assignment_id)
                                   .where(Outcome.assignment_id.in_([a.id for a, _, _ in recent]))).scalars().all())

    def ended(a: Assignment) -> str | None:
        if a.is_active:
            return None
        return "completed" if a.id in completed_ids else "reassigned"

    return {
        "caseloads": underwriters(db),
        "counts_by_status": {s: counts.get(s, 0) for s in ("NEW", "ROUTED", "QUEUED", "COMPLETED")},
        "learning": learning(db),
        "recent_assignments": [{"application_id": a.application_id, "applicant_name": ap, "underwriter_name": n,
                                "method": a.method, "is_active": a.is_active, "ended": ended(a),
                                "created_at": iso(a.created_at)}
                               for a, n, ap in recent],
    }


@app.get("/api/notifications")
def notifications(recipient: str | None = None, db: Session = Depends(get_db)):
    q = select(Notification).order_by(Notification.id.desc()).limit(50)
    if recipient:
        q = q.where(Notification.recipient == recipient)
    return [{"recipient": n.recipient, "application_id": n.application_id, "message": n.message,
             "created_at": iso(n.created_at)} for n in db.execute(q).scalars().all()]
