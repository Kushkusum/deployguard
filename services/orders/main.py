import os
import time
import redis
from fastapi import FastAPI

app = FastAPI()
r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

# BUG_MODE simulates the "bad canary" version we'll deploy later
BUG_MODE = os.getenv("BUG_MODE", "false").lower() == "true"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/orders")
def get_orders():
    if BUG_MODE:
        time.sleep(2)  # simulated latency regression
    count = r.incr("orders_seen")
    return {"orders_seen": count, "bug_mode": BUG_MODE}

@app.post("/orders")
def create_order():
    if BUG_MODE and int(r.get("orders_seen") or 0) % 5 == 0:
        raise Exception("simulated failure")  # simulated error rate regression
    order_id = r.incr("order_id_counter")
    return {"order_id": order_id, "status": "created"}
