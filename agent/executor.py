import os
import requests
import chromadb
from chromadb.utils import embedding_functions

PROMETHEUS_URL = "http://localhost:9090"  # port-forwarded: kubectl port-forward svc/prometheus-svc 9090:9090
CHROMA_PATH = os.getenv("CHROMA_DB_PATH", os.path.expanduser("~/deployguard_data/chroma_db"))


def query_prometheus(promql):
    resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql})
    resp.raise_for_status()
    return resp.json()["data"]["result"]


def scalar(result, default=0.0):
    # Prometheus returns a list of {"metric": {...}, "value": [timestamp, "stringValue"]}.
    # We only ever query aggregates (avg/sum with no "by"), so there's at most one series.
    if not result:
        return default
    return float(result[0]["value"][1])


def get_canary_signals():
    canary_latency_sum = scalar(query_prometheus('avg(orders_request_latency_seconds_sum{track="canary"})'))
    canary_latency_count = scalar(query_prometheus('avg(orders_request_latency_seconds_count{track="canary"})'))
    stable_latency_sum = scalar(query_prometheus('avg(orders_request_latency_seconds_sum{track="stable"})'))
    stable_latency_count = scalar(query_prometheus('avg(orders_request_latency_seconds_count{track="stable"})'))

    canary_errors = scalar(query_prometheus('sum(orders_requests_total{track="canary", status="error"})'))
    canary_total = scalar(query_prometheus('sum(orders_requests_total{track="canary"})'))

    return {
        "canary_latency": round(canary_latency_sum / canary_latency_count, 4) if canary_latency_count else 0.0,
        "stable_latency": round(stable_latency_sum / stable_latency_count, 4) if stable_latency_count else 0.0,
        "canary_errors": canary_errors,
        "canary_total": canary_total,
    }


def retrieve_similar_incidents(query_text, n=3):
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    embed_fn = embedding_functions.OllamaEmbeddingFunction(
        url="http://localhost:11434/api/embeddings", model_name="nomic-embed-text"
    )
    collection = client.get_collection(name="incidents", embedding_function=embed_fn)
    results = collection.query(query_texts=[query_text], n_results=n)
    return results
