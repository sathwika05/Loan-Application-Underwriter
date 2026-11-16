"""In-memory run registry so the UI can poll step + tool status for long operations."""
import copy
import threading
import uuid
from datetime import datetime, timezone

_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(kind: str, steps: list[str], tool_limits: dict[str, int], application_id: int, request_id: str) -> dict:
    run_id = uuid.uuid4().hex
    run = {
        "run_id": run_id, "kind": kind, "application_id": application_id, "request_id": request_id,
        "status": "running", "created_at": _now(), "finished_at": None,
        "steps": [{"name": s, "status": "pending"} for s in steps],
        "tools": {t: {"calls": 0, "limit": lim, "status": "idle"} for t, lim in tool_limits.items()},
        "result": None, "error": None,
    }
    with _lock:
        _runs[run_id] = run
    return copy.deepcopy(run)


def set_step(run_id: str, name: str, status: str, detail: str | None = None) -> None:
    with _lock:
        for s in _runs[run_id]["steps"]:
            if s["name"] == name:
                s["status"] = status
                if detail:
                    s["detail"] = detail


def tool_call(run_id: str, tool: str) -> bool:
    """Register a tool call. Returns False when the per-tool limit is reached."""
    with _lock:
        t = _runs[run_id]["tools"][tool]
        if t["calls"] >= t["limit"]:
            t["status"] = "limit_reached"
            return False
        t["calls"] += 1
        t["status"] = "running"
        return True


def tool_done(run_id: str, tool: str, ok: bool = True) -> None:
    with _lock:
        _runs[run_id]["tools"][tool]["status"] = "done" if ok else "error"


def finish(run_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
    with _lock:
        run = _runs[run_id]
        for s in run["steps"]:
            if s["status"] in ("pending", "running"):
                s["status"] = "skipped" if status == "succeeded" else s["status"]
        run.update(status=status, result=result, error=error, finished_at=_now())


def get(run_id: str) -> dict | None:
    with _lock:
        run = _runs.get(run_id)
        return copy.deepcopy(run) if run else None
