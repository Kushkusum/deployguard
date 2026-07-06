# orders service: FastAPI + Redis, with a BUG_MODE toggle for canary testing.
# Demo scene 2 - regression release, canary running BUG_MODE=true.
import os
import time
import redis
from fastapi import FastAPI, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI()
r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

# BUG_MODE simulates the "bad canary" version we'll deploy later
BUG_MODE = os.getenv("BUG_MODE", "false").lower() == "true"

# --- chalkboard entries ---
REQUEST_COUNT = Counter("orders_requests_total", "Total requests", ["endpoint", "status"])
REQUEST_LATENCY = Histogram("orders_request_latency_seconds", "Request latency", ["endpoint"])

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/orders")
def get_orders():
    start = time.time()
    if BUG_MODE:
        time.sleep(2)  # simulated latency regression
    count = r.incr("orders_seen")
    REQUEST_LATENCY.labels(endpoint="/orders").observe(time.time() - start)
    REQUEST_COUNT.labels(endpoint="/orders", status="success").inc()
    return {"orders_seen": count, "bug_mode": BUG_MODE}

@app.post("/orders")
def create_order():
    start = time.time()
    try:
        if BUG_MODE and int(r.get("orders_seen") or 0) % 5 == 0:
            raise Exception("simulated failure")  # simulated error rate regression
        order_id = r.incr("order_id_counter")
        REQUEST_LATENCY.labels(endpoint="/orders_post").observe(time.time() - start)
        REQUEST_COUNT.labels(endpoint="/orders_post", status="success").inc()
        return {"order_id": order_id, "status": "created"}
    except Exception as e:
        REQUEST_COUNT.labels(endpoint="/orders_post", status="error").inc()
        raise
