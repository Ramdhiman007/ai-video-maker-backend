"""
RQ Worker Entrypoint
Run with: python worker.py
Railway will deploy this as a separate "Worker" service alongside the web service.
"""

import os
from dotenv import load_dotenv

load_dotenv()

import redis
from rq import Worker, Queue, Connection

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

if __name__ == "__main__":
    conn = redis.from_url(REDIS_URL)
    queues = ["default", "video"]
    print(f"[worker] Connecting to Redis: {REDIS_URL[:30]}...")
    print(f"[worker] Listening on queues: {queues}")
    with Connection(conn):
        w = Worker(queues)
        w.work(with_scheduler=True)
