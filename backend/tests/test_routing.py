"""Unit tests for the deterministic parts of routing (no database, no OpenAI)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import runs
from app.graph import MAX_DELTA, ROUTE_STEPS, ROUTE_TOOL_LIMITS, guard
from app.rules import eligibility_reasons, score


def uw(**kw):
    base = dict(id=1, name="A", active=True, loan_types=["mortgage"], regions=["East"], max_amount=500_000,
                max_caseload=5, avg_turnaround_hours=20, quality_score=0.9)
    base.update(kw)
    return SimpleNamespace(**base)


def app(**kw):
    base = dict(loan_type="mortgage", region="East", amount=300_000, credit_score=700, priority="normal",
                notes="", sla_due_at=datetime.now(timezone.utc) + timedelta(hours=72))
    base.update(kw)
    return SimpleNamespace(**base)


def test_eligible_underwriter_has_no_reasons():
    assert eligibility_reasons(uw(), app(), load=0) == []


def test_each_hard_rule_produces_a_reason():
    reasons = eligibility_reasons(uw(active=False, loan_types=["auto"], regions=["West"], max_amount=100_000),
                                  app(), load=5)
    assert len(reasons) == 5


def test_scoring_is_deterministic_and_prefers_free_capacity():
    a = app()
    s1 = score(uw(), a, load=0, recent=0, avg_recent=0)
    s2 = score(uw(), a, load=0, recent=0, avg_recent=0)
    busy = score(uw(), a, load=4, recent=0, avg_recent=0)
    assert s1["rules_score"] == s2["rules_score"]
    assert busy["rules_score"] < s1["rules_score"]


def test_fairness_penalises_recent_overallocation():
    a = app()
    fair = score(uw(), a, load=0, recent=1, avg_recent=1)
    over = score(uw(), a, load=0, recent=5, avg_recent=1)
    assert over["components"]["fairness"] < fair["components"]["fairness"]


def _guard_state(ai_output):
    run = runs.create("route", ROUTE_STEPS, ROUTE_TOOL_LIMITS, 1, "test")
    scored = [
        {"underwriter_id": 1, "name": "A", "rules_score": 0.90, "components": {"x": 1, "y": 1}, "load": 0,
         "max_caseload": 5, "sla_risk": "low"},
        {"underwriter_id": 2, "name": "B", "rules_score": 0.85, "components": {"x": 1, "y": 1}, "load": 0,
         "max_caseload": 5, "sla_risk": "low"},
    ]
    return {"run_id": run["run_id"], "scored": scored, "excluded": [], "ai_output": ai_output,
            "application": app()}


def test_guard_clamps_ai_delta_to_bounds():
    out = guard(_guard_state({"adjustments": [{"underwriter_id": 2, "delta": 5.0, "reason": "x"}],
                              "chosen_underwriter_id": 2, "rationale": "B is best"}))
    b = next(c for c in out["final"] if c["underwriter_id"] == 2)
    assert b["ai_delta"] == MAX_DELTA
    assert out["chosen"]["underwriter_id"] == 2  # 0.85 + 0.10 > 0.90


def test_guard_ignores_candidates_outside_shortlist():
    out = guard(_guard_state({"adjustments": [{"underwriter_id": 99, "delta": 0.1, "reason": "x"}],
                              "chosen_underwriter_id": 99, "rationale": "pick 99"}))
    assert out["chosen"]["underwriter_id"] == 1
    assert "pick 99" not in out["rationale"]  # LLM rationale dropped when its pick is not the winner


def test_guard_falls_back_to_rules_when_llm_missing():
    out = guard(_guard_state(None))
    assert out["chosen"]["underwriter_id"] == 1
    assert out["rationale"]


def test_tool_call_limit_is_enforced():
    run = runs.create("route", ROUTE_STEPS, {"openai.optimize": 1}, 1, "test")
    assert runs.tool_call(run["run_id"], "openai.optimize") is True
    assert runs.tool_call(run["run_id"], "openai.optimize") is False
    assert runs.get(run["run_id"])["tools"]["openai.optimize"]["status"] == "limit_reached"


# ---------- adaptive optimisation ----------
from app.learning import ema, outcome_quality, tune_weights  # noqa: E402
from app.rules import WEIGHTS, humanize_ids  # noqa: E402


def test_outcome_quality_penalises_rework_and_sla_miss():
    assert outcome_quality(sla_met=True, rework=False) == 1.0
    assert outcome_quality(sla_met=False, rework=False) == 0.8
    assert outcome_quality(sla_met=True, rework=True) == 0.5


def test_ema_moves_toward_new_value():
    assert ema(0.9, 0.5, alpha=0.2) == 0.9 * 0.8 + 0.5 * 0.2


def test_tune_weights_raises_sla_weight_when_sla_misses_and_stays_normalised():
    tuned = tune_weights(sla_miss_rate=0.5, rework_rate=0.0)
    assert tuned["sla"] > WEIGHTS["sla"]
    assert abs(sum(tuned.values()) - 1.0) < 0.01


def test_tune_weights_raises_expertise_weight_on_rework():
    assert tune_weights(sla_miss_rate=0.0, rework_rate=0.5)["expertise"] > WEIGHTS["expertise"]


def test_tune_weights_with_no_problems_keeps_defaults():
    assert tune_weights(0.0, 0.0) == {k: round(v, 3) for k, v in WEIGHTS.items()}


def test_learned_weights_change_the_ranking():
    a = app()
    fast_low_quality = uw(id=1, avg_turnaround_hours=5, quality_score=0.5)
    slow_high_quality = uw(id=2, avg_turnaround_hours=60, quality_score=1.0)
    sla_heavy = tune_weights(sla_miss_rate=1.0, rework_rate=0.0)
    s_fast = score(fast_low_quality, a, 0, 0, 0, sla_heavy)["rules_score"]
    s_slow = score(slow_high_quality, a, 0, 0, 0, sla_heavy)["rules_score"]
    assert s_fast > s_slow


def test_humanize_replaces_ids_with_names():
    out = humanize_ids("Underwriter 2 beats Underwriter 1; candidate #3 excluded.", {1: "Priya", 2: "Raj", 3: "Maria"})
    assert out == "Raj beats Priya; Maria excluded."
