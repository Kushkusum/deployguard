import os
import re
from datetime import datetime, timezone
from executor import get_canary_signals, retrieve_similar_incidents
from inspector import decide


def parse_decision(inspector_output):
    match = re.search(r"DECISION:\s*(\w+)", inspector_output, re.IGNORECASE)
    decision = match.group(1).lower() if match else "hold"
    justification_match = re.search(r"JUSTIFICATION:\s*(.+)", inspector_output, re.IGNORECASE | re.DOTALL)
    justification = justification_match.group(1).strip() if justification_match else inspector_output.strip()
    return decision, justification


def main():
    signals = get_canary_signals()
    query_text = f"Canary latency {signals['canary_latency']}, errors {signals['canary_errors']}/{signals['canary_total']}"
    similar = retrieve_similar_incidents(query_text)
    similar_text = "\n".join(similar["documents"][0])

    inspector_output = decide(signals, similar_text)
    decision, justification = parse_decision(inspector_output)

    report = f"""# DeployGuard Incident Report

**Timestamp:** {datetime.now(timezone.utc).isoformat()}
**Decision:** {decision.upper()}
**Justification:** {justification}

## Signals
- Canary latency: {signals['canary_latency']}s
- Stable latency: {signals['stable_latency']}s
- Canary errors: {signals['canary_errors']} / {signals['canary_total']}

## Retrieved similar incidents
{similar_text}

## Full Inspector output
{inspector_output}
"""
    with open("incident_report.md", "w") as f:
        f.write(report)

    # hand the decision back to the workflow so later steps can branch on it
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"decision={decision}\n")

    print(report)


if __name__ == "__main__":
    main()
