"""Adaptive optimisation: learn from outcomes and refine routing over time.

Two feedback loops, both auditable:
1. Per-underwriter: quality_score and avg_turnaround_hours move toward each new outcome
   (exponential moving average). Routing reads these on the next run.
2. Global: scoring weights are re-derived from recent SLA-miss and rework rates and saved as a
   new version in routing_weights. score_rules always uses the latest version.
"""
from sqlalchemy import func, select

from .models import AuditEvent, Outcome, RoutingWeights, Underwriter
from .rules import WEIGHTS as DEFAULT_WEIGHTS

ALPHA = 0.2            # EMA learning rate per outcome
WINDOW = 50            # outcomes considered when tuning weights
MIN_OUTCOMES = 3       # don't tune weights on fewer outcomes than this
MIN_CHANGE = 0.005     # ignore weight changes smaller than this


# ---------- pure functions (unit-tested) ----------
def outcome_quality(sla_met: bool, rework: bool) -> float:
    """1.0 = on time, no rework. Rework halves it; a missed SLA costs 20%."""
    return (0.5 if rework else 1.0) * (1.0 if sla_met else 0.8)


def ema(old: float, new: float, alpha: float = ALPHA) -> float:
    return (1 - alpha) * old + alpha * new


def tune_weights(sla_miss_rate: float, rework_rate: float, base: dict[str, float] | None = None) -> dict[str, float]:
    """More SLA misses -> weight SLA risk more. More rework -> weight expertise more. Sums to 1."""
    base = dict(base or DEFAULT_WEIGHTS)
    raw = {**base, "sla": base["sla"] * (1 + sla_miss_rate), "expertise": base["expertise"] * (1 + rework_rate)}
    total = sum(raw.values())
    return {k: round(v / total, 3) for k, v in raw.items()}


# ---------- DB helpers ----------
def current_weights(db) -> tuple[int, dict[str, float]]:
    row = db.execute(select(RoutingWeights).order_by(RoutingWeights.version.desc()).limit(1)).scalar_one_or_none()
    return (row.version, row.weights) if row else (0, dict(DEFAULT_WEIGHTS))


def outcome_stats(db, window: int = WINDOW) -> dict:
    rows = db.execute(select(Outcome.sla_met, Outcome.rework).order_by(Outcome.id.desc()).limit(window)).all()
    n = len(rows)
    return {
        "sample_size": n,
        "sla_miss_rate": round(sum(1 for s, _ in rows if not s) / n, 3) if n else 0.0,
        "rework_rate": round(sum(1 for _, r in rows if r) / n, 3) if n else 0.0,
        "total_outcomes": db.execute(select(func.count()).select_from(Outcome)).scalar_one(),
    }


def learn_from_outcome(db, uw: Underwriter, outcome: Outcome, actor: str) -> dict:
    """Apply both feedback loops. Caller commits."""
    before = {"quality_score": uw.quality_score, "avg_turnaround_hours": uw.avg_turnaround_hours}
    uw.quality_score = round(max(0.0, min(1.0, ema(uw.quality_score, outcome_quality(outcome.sla_met, outcome.rework)))), 2)
    uw.avg_turnaround_hours = round(ema(uw.avg_turnaround_hours, outcome.turnaround_hours), 2)
    after = {"quality_score": uw.quality_score, "avg_turnaround_hours": uw.avg_turnaround_hours}
    db.add(AuditEvent(application_id=outcome.application_id, event_type="UNDERWRITER_LEARNED", actor=actor,
                      payload={"underwriter_id": uw.id, "name": uw.name, "before": before, "after": after}))

    db.flush()
    stats = outcome_stats(db)
    version, weights = current_weights(db)
    result = {"underwriter": {"name": uw.name, "before": before, "after": after},
              "weights_version": version, "weights_changed": False, "stats": stats}
    if stats["sample_size"] < MIN_OUTCOMES:
        return result
    new = tune_weights(stats["sla_miss_rate"], stats["rework_rate"])
    if all(abs(new[k] - weights.get(k, 0)) < MIN_CHANGE for k in new):
        return result
    reason = (f"Re-tuned from last {stats['sample_size']} outcomes: SLA miss rate {stats['sla_miss_rate']:.0%}, "
              f"rework rate {stats['rework_rate']:.0%}")
    db.add(RoutingWeights(version=version + 1, weights=new, reason=reason, stats=stats))
    db.add(AuditEvent(application_id=outcome.application_id, event_type="WEIGHTS_UPDATED", actor=actor,
                      payload={"from_version": version, "to_version": version + 1, "from": weights, "to": new,
                               "reason": reason}))
    result.update(weights_version=version + 1, weights_changed=True, new_weights=new, reason=reason)
    return result
