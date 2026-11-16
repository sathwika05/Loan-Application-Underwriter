"""Deterministic eligibility + scoring (rules engine)."""
import re
from datetime import datetime, timedelta, timezone

SLA_HOURS = {"urgent": 24, "high": 48, "normal": 72}
WEIGHTS = {"expertise": 0.3, "capacity": 0.3, "sla": 0.2, "fairness": 0.2}


def sla_due(priority: str) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SLA_HOURS[priority])


def eligibility_reasons(uw, app, load: int) -> list[str]:
    reasons = []
    if not uw.active:
        reasons.append("Inactive / on leave")
    if app.loan_type not in uw.loan_types:
        reasons.append(f"No {app.loan_type} expertise")
    if app.region not in uw.regions:
        reasons.append(f"Not licensed for {app.region}")
    if app.amount > uw.max_amount:
        reasons.append(f"Amount ${app.amount:,.0f} exceeds authority ${uw.max_amount:,.0f}")
    if load >= uw.max_caseload:
        reasons.append(f"At capacity ({load}/{uw.max_caseload})")
    return reasons


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score(uw, app, load: int, recent: int, avg_recent: float, weights: dict[str, float] | None = None) -> dict:
    weights = weights or WEIGHTS
    now = datetime.now(timezone.utc)
    remaining = max(1.0, (app.sla_due_at - now).total_seconds() / 3600)
    projected = uw.avg_turnaround_hours * (1 + load / uw.max_caseload)
    ratio = projected / remaining
    components = {
        "expertise": _clamp(0.5 + (0.25 if uw.loan_types[0] == app.loan_type else 0) + 0.25 * uw.quality_score),
        "capacity": _clamp(1 - load / uw.max_caseload),
        "sla": _clamp(1.2 - ratio),
        "fairness": _clamp(1 - 0.25 * max(0.0, recent - avg_recent)),
    }
    rules_score = round(sum(weights[k] * v for k, v in components.items()), 4)
    return {
        "underwriter_id": uw.id,
        "name": uw.name,
        "load": load,
        "max_caseload": uw.max_caseload,
        "recent_assignments": recent,
        "sla_risk": "high" if ratio > 1 else "medium" if ratio > 0.75 else "low",
        "components": {k: round(v, 3) for k, v in components.items()},
        "rules_score": rules_score,
    }


def humanize_ids(text: str, names: dict[int, str]) -> str:
    """Safety net for explainability: replace 'Underwriter 2' / 'underwriter_id 2' with the person's name."""
    def sub(m: re.Match) -> str:
        return names.get(int(m.group(1)), m.group(0))
    return re.sub(r"\b(?:[Uu]nderwriter(?:_id)?|[Cc]andidate|ID)\s*#?\s*(\d+)\b", sub, text)


def template_rationale(app, chosen: dict, runner_up: dict | None, excluded_count: int) -> str:
    c = chosen["components"]
    top = sorted(c.items(), key=lambda kv: -kv[1])[:2]
    text = (f"{chosen['name']} was selected for this {app.loan_type} loan (${app.amount:,.0f}, {app.region}). "
            f"Strongest factors: {top[0][0]} ({top[0][1]:.2f}) and {top[1][0]} ({top[1][1]:.2f}); "
            f"current caseload {chosen['load']}/{chosen['max_caseload']}, SLA risk {chosen['sla_risk']}.")
    if runner_up:
        text += f" Runner-up {runner_up['name']} scored {runner_up['final_score']:.3f} vs {chosen['final_score']:.3f}."
    if excluded_count:
        text += f" {excluded_count} underwriter(s) were excluded by eligibility rules."
    return text
