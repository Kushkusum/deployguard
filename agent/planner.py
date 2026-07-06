def plan():
    return {
        "checks": ["latency_comparison", "error_rate", "similar_incidents"],
        "query_text_template": "Canary showing latency {latency_ratio}x stable, error rate {error_rate}%.",
    }
