from executor import get_canary_signals, retrieve_similar_incidents
from inspector import decide

signals = get_canary_signals()
query_text = f"Canary latency {signals['canary_latency']}, errors {signals['canary_errors']}/{signals['canary_total']}"
similar = retrieve_similar_incidents(query_text)
similar_text = "\n".join(similar["documents"][0])

print("--- signals ---")
print(signals)
print("--- retrieved incidents ---")
print(similar_text)
print("--- decision ---")

result = decide(signals, similar_text)
print(result)
