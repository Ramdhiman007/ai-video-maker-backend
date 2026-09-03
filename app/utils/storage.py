import os
import json
import uuid
import shutil
from typing import Dict, Optional, Any
from pathlib import Path
from app.config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR

# In-memory and persistent state storage
_MEDIA_STORE: Dict[str, Dict[str, Any]] = {}
_TASK_STORE: Dict[str, Dict[str, Any]] = {}

def get_media_dir() -> Path:
    return UPLOAD_DIR

def get_output_dir() -> Path:
    return OUTPUT_DIR

def get_temp_dir() -> Path:
    return TEMP_DIR

def save_media_metadata(media_id: str, data: Dict[str, Any]):
    _MEDIA_STORE[media_id] = data

def get_media_metadata(media_id: str) -> Optional[Dict[str, Any]]:
    return _MEDIA_STORE.get(media_id)

def get_all_media_metadata() -> Dict[str, Dict[str, Any]]:
    return _MEDIA_STORE

def save_task_progress(task_id: str, progress_data: Dict[str, Any]):
    _TASK_STORE[task_id] = progress_data

def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    return _TASK_STORE.get(task_id)

def update_task_step(task_id: str, step_name: str, progress_pct: int, details: Optional[str] = None):
    task = _TASK_STORE.get(task_id)
    if not task:
        task = {
            "task_id": task_id,
            "status": "processing",
            "progress": 0,
            "current_step": step_name,
            "step_details": [],
            "result_video_url": None,
            "error": None
        }
        _TASK_STORE[task_id] = task

    task["progress"] = progress_pct
    task["current_step"] = step_name
    
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
            "details": details or ""
        })

    # Mark previous steps completed
    for step in task.get("step_details", []):
        if step["name"] != step_name and step["status"] == "in_progress":
            step["status"] = "completed"

def cleanup_temp_dir(sub_dir: Optional[Path] = None):
    target = sub_dir or TEMP_DIR
    if target.exists():
        for item in target.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass
