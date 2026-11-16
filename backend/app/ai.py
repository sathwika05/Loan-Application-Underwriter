"""OpenAI calls: bounded routing optimisation."""
import json
import logging

from openai import OpenAI

from .config import OPENAI_API_KEY, OPENAI_MODEL

log = logging.getLogger(__name__)
client = OpenAI(api_key=OPENAI_API_KEY, timeout=20, max_retries=1) if OPENAI_API_KEY else None

OPTIMIZE_SYSTEM = """You are an underwriting operations optimizer.
You receive a loan application and a shortlist of ELIGIBLE underwriters already scored by a deterministic rules engine.
Adjust each candidate's score by a delta between -0.10 and +0.10 for contextual fit (profile vs. application notes),
fairness (spread work, avoid overloading), and SLA risk. You may ONLY choose from the shortlist.
Never consider names, gender, ethnicity, age or any protected characteristic.
Return JSON: {"adjustments":[{"underwriter_id":int,"delta":float,"reason":str}],
"chosen_underwriter_id":int,"rationale":str}
The rationale is 2-4 sentences for the loan submitter, citing concrete factors (expertise, caseload, SLA risk, fit).
Always refer to underwriters by their "name" field (e.g. "Maria Gonzalez"), never as "Underwriter 3" or by id."""


def optimize(application: dict, shortlist: list[dict]) -> dict:
    if client is None:
        raise RuntimeError("OPENAI_API_KEY not configured")
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,  # determinism: same input -> same adjustments (as far as the API allows)
        seed=42,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": OPTIMIZE_SYSTEM},
            {"role": "user", "content": json.dumps({"application": application, "shortlist": shortlist})},
        ],
    )
    return json.loads(resp.choices[0].message.content or "{}")
