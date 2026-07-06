import re
import ollama

DECISION_PROMPT = """You are an SRE reviewing a canary deployment.

Facts already established from monitoring data (treat these as certain, do not re-derive them):
- Canary avg latency: {canary_latency}s vs stable avg latency: {stable_latency}s ({latency_ratio}x)
- Canary errors: {canary_errors} out of {canary_total} requests ({error_rate_pct}% error rate)
- Latency ratio exceeds 3x: {latency_over_3x}
- Error rate exceeds 10%: {error_over_10pct}

Similar past incidents:
{similar_incidents}

Your only job: does any retrieved incident above describe a transient/false-alarm cause
(warm-up, a shared infra event, or an external dependency issue affecting both tracks
equally) that plausibly explains the current facts? Answer in exactly this format:
TRANSIENT_CAUSE_MATCHED: <yes|no>
JUSTIFICATION: <one or two sentences explaining the signals and which past incident, if any, this resembles>
"""

# Thresholds are computed here in Python, not by the model. Testing showed the
# 1.5B model is unreliable even at simple numeric comparisons (e.g. it called
# a 0.31x ratio "over 3x", and separately failed to chain correct yes/no facts
# into the matching decision). The model's job is narrowed to the one genuinely
# qualitative judgment: does a retrieved incident's narrative match the current
# situation as a transient/false-alarm cause. Everything numeric is exact.
_YES = re.compile(r"\byes\b", re.IGNORECASE)


def _apply_decision_rule(latency_over_3x, error_over_10pct, transient_matched):
    if (latency_over_3x or error_over_10pct) and not transient_matched:
        return "rollback"
    if not latency_over_3x and not error_over_10pct:
        return "promote"
    return "hold"


def decide(signals, similar_incidents_text):
    stable_latency = signals["stable_latency"] or 0.0001  # avoid divide-by-zero
    latency_ratio = round(signals["canary_latency"] / stable_latency, 2)
    error_rate_pct = round((signals["canary_errors"] / signals["canary_total"]) * 100, 1) if signals["canary_total"] else 0.0
    latency_over_3x = latency_ratio > 3
    error_over_10pct = error_rate_pct > 10

    prompt = DECISION_PROMPT.format(
        canary_latency=signals["canary_latency"],
        stable_latency=signals["stable_latency"],
        latency_ratio=latency_ratio,
        canary_errors=signals["canary_errors"],
        canary_total=signals["canary_total"],
        error_rate_pct=error_rate_pct,
        latency_over_3x="yes" if latency_over_3x else "no",
        error_over_10pct="yes" if error_over_10pct else "no",
        similar_incidents=similar_incidents_text,
    )
    response = ollama.chat(
        model="qwen2.5:1.5b",
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.2},
    )
    raw = response["message"]["content"]

    match = re.search(r"TRANSIENT_CAUSE_MATCHED:\s*(\w+)", raw, re.IGNORECASE)
    transient_matched = bool(match and _YES.search(match.group(1)))
    decision = _apply_decision_rule(latency_over_3x, error_over_10pct, transient_matched)

    justification_match = re.search(r"JUSTIFICATION:\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
    justification = justification_match.group(1).strip() if justification_match else raw.strip()

    return f"DECISION: {decision}\nJUSTIFICATION: {justification}"
