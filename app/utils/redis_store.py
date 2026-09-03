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


# ─── In-memory fallback (local dev) ──────────────────────────────────────────
_MEDIA_STORE: Dict[str, Dict[str, Any]] = {}
_TASK_STORE: Dict[str, Dict[str, Any]] = {}

_MEDIA_TTL = 60 * 60 * 25  # 25 hours
_TASK_TTL  = 60 * 60 * 25  # 25 hours


# ─── Media Metadata ──────────────────────────────────────────────────────────

def save_media_metadata(media_id: str, data: Dict[str, Any]):
    r = _get_redis()
    if r:
        r.setex(f"media:{media_id}", _MEDIA_TTL, json.dumps(data))
    else:
        _MEDIA_STORE[media_id] = data


def get_media_metadata(media_id: str) -> Optional[Dict[str, Any]]:
    r = _get_redis()
    if r:
        raw = r.get(f"media:{media_id}")
        return json.loads(raw) if raw else None
    return _MEDIA_STORE.get(media_id)


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
    return dict(_MEDIA_STORE)


# ─── Task Progress ────────────────────────────────────────────────────────────

def save_task_progress(task_id: str, progress_data: Dict[str, Any]):
    r = _get_redis()
    if r:
        r.setex(f"task:{task_id}", _TASK_TTL, json.dumps(progress_data))
    else:
        _TASK_STORE[task_id] = progress_data


def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    r = _get_redis()
    if r:
        raw = r.get(f"task:{task_id}")
        return json.loads(raw) if raw else None
    return _TASK_STORE.get(task_id)


def update_task_step(task_id: str, step_name: str, progress_pct: int, details: Optional[str] = None):
    task = get_task_progress(task_id)
    if not task:
        task = {
            "task_id": task_id,
            "status": "processing",
            "progress": 0,
            "current_step": step_name,
            "step_details": [],
            "result_video_url": None,
            "error": None,
        }

    task["progress"] = progress_pct
    task["current_step"] = step_name
    task["status"] = "processing"

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
