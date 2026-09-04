"""
Redis-backed Task and Media Metadata Store
Replaces the in-memory Python dict store from storage.py.
Falls back to in-memory dicts if Redis is not configured (local dev).
"""

import os
import json
from typing import Dict, Optional, Any

# ─── Try Redis connection ─────────────────────────────────────────────────────
_redis_client = None

def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        _redis_client = redis.from_url(redis_url, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        print(f"[redis_store] Redis connection failed, using in-memory fallback: {e}")
        return None


# ─── Multi-process fallback (SQLite WAL mode when Redis is not linked) ────────
import sqlite3
from app.config import TEMP_DIR

_DB_PATH = TEMP_DIR / "app_store.db"

def _init_sqlite():
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(_DB_PATH, timeout=10.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS store (key TEXT PRIMARY KEY, val TEXT)")
    except Exception as e:
        print(f"[redis_store] SQLite fallback init warning: {e}")

_init_sqlite()

def _sqlite_set(key: str, val: str):
    try:
        with sqlite3.connect(_DB_PATH, timeout=10.0) as conn:
            conn.execute("INSERT OR REPLACE INTO store (key, val) VALUES (?, ?)", (key, val))
    except Exception as e:
        print(f"[redis_store] SQLite write error: {e}")

def _sqlite_get(key: str) -> Optional[str]:
    try:
        with sqlite3.connect(_DB_PATH, timeout=10.0) as conn:
            cur = conn.cursor()
            cur.execute("SELECT val FROM store WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"[redis_store] SQLite read error: {e}")
        return None

_MEDIA_TTL = 60 * 60 * 25  # 25 hours
_TASK_TTL  = 60 * 60 * 25  # 25 hours


# ─── Media Metadata ──────────────────────────────────────────────────────────

def save_media_metadata(media_id: str, data: Dict[str, Any]):
    r = _get_redis()
    if r:
        r.setex(f"media:{media_id}", _MEDIA_TTL, json.dumps(data))
    else:
        _sqlite_set(f"media:{media_id}", json.dumps(data))


def get_media_metadata(media_id: str) -> Optional[Dict[str, Any]]:
    r = _get_redis()
    if r:
        raw = r.get(f"media:{media_id}")
        return json.loads(raw) if raw else None
    raw = _sqlite_get(f"media:{media_id}")
    return json.loads(raw) if raw else None


def get_all_media_metadata() -> Dict[str, Dict[str, Any]]:
    r = _get_redis()
    if r:
        keys = r.keys("media:*")
        result = {}
        for k in keys:
            raw = r.get(k)
            if raw:
                media_id = k.replace("media:", "", 1)
                result[media_id] = json.loads(raw)
        return result
    result = {}
    try:
        with sqlite3.connect(_DB_PATH, timeout=10.0) as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, val FROM store WHERE key LIKE 'media:%'")
            for k, val in cur.fetchall():
                media_id = k.replace("media:", "", 1)
                result[media_id] = json.loads(val)
    except Exception:
        pass
    return result


# ─── Task Progress ────────────────────────────────────────────────────────────

def save_task_progress(task_id: str, progress_data: Dict[str, Any]):
    r = _get_redis()
    if r:
        r.setex(f"task:{task_id}", _TASK_TTL, json.dumps(progress_data))
    else:
        _sqlite_set(f"task:{task_id}", json.dumps(progress_data))


def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    r = _get_redis()
    if r:
        raw = r.get(f"task:{task_id}")
        return json.loads(raw) if raw else None
    raw = _sqlite_get(f"task:{task_id}")
    return json.loads(raw) if raw else None


def update_task_step(task_id: str, step_name: str, progress_pct: int, details: Optional[str] = None, agent_log: Optional[Dict[str, str]] = None):
    task = get_task_progress(task_id)
    if not task:
        task = {
            "task_id": task_id,
            "status": "processing",
            "progress": 0,
            "current_step": step_name,
            "step_details": [],
            "agent_logs": [],
            "result_video_url": None,
            "error": None,
        }

    task["progress"] = progress_pct
    task["current_step"] = step_name
    task["status"] = "processing"
    task.setdefault("agent_logs", [])

    if agent_log:
        task["agent_logs"].append(agent_log)
        # Keep last 30 logs
        if len(task["agent_logs"]) > 30:
            task["agent_logs"] = task["agent_logs"][-30:]

    # Update step checklist
    found = False
    for step in task.get("step_details", []):
        if step["name"] == step_name:
            step["status"] = "in_progress" if progress_pct < 100 else "completed"
            if details:
                step["details"] = details
            found = True
            break

    if not found:
        task.setdefault("step_details", []).append({
            "name": step_name,
            "status": "in_progress",
            "details": details or "",
        })

    # Mark earlier steps as completed
    for step in task.get("step_details", []):
        if step["name"] != step_name and step["status"] == "in_progress":
            step["status"] = "completed"

    save_task_progress(task_id, task)

