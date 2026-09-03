import os
import shutil
from pathlib import Path
from typing import Optional

from app.config import OUTPUT_DIR, TEMP_DIR, UPLOAD_DIR
from app.models.schemas import Timeline
from app.utils.redis_store import update_task_step, save_task_progress, get_task_progress
from app.services.ffmpeg_engine import (
    prepare_segment_clip,
    concatenate_clips_with_transitions,
    mix_audio_track,
)


def render_video_from_timeline(task_id: str, timeline: Timeline, music_volume: float = 0.8) -> str:
    """
    Renders the complete video from a Timeline object.
    Updates real-time progress in Redis (or in-memory fallback).
    Returns the relative output URL (may be overwritten by R2 URL in endpoints.py).
    """
    work_dir = TEMP_DIR / f"render_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        update_task_step(task_id, "Selecting best moments", 40, "Filtered high-quality frames and beat points")
        update_task_step(task_id, "Creating timeline", 50, f"Structured {len(timeline.segments)} segments")
        update_task_step(task_id, "Applying effects", 65, "Rendering Ken Burns pan/zoom and text overlays")

        clip_paths = []
        transitions = []
        durations = []

        total_segs = len(timeline.segments)
        for idx, seg in enumerate(timeline.segments):
            pct = 65 + int((idx / max(total_segs, 1)) * 15)
            update_task_step(
                task_id,
                "Applying effects",
                pct,
                f"Processing segment {idx + 1}/{total_segs}: {seg.file} ({seg.effect})"
            )

            clip_path = prepare_segment_clip(
                segment=seg,
                target_res=timeline.resolution,
                out_dir=work_dir,
                index=idx,
            )
            clip_paths.append(clip_path)
            transitions.append(seg.transition)
            durations.append(seg.duration)

        update_task_step(task_id, "Adding transitions", 82, "Merging clips with crossfades and motion cuts")

        raw_concat = work_dir / "concat_raw.mp4"
        concatenate_clips_with_transitions(
            clip_paths=clip_paths,
            transitions=transitions,
            durations=durations,
            out_video_path=raw_concat,
            target_width=timeline.resolution[0],
            target_height=timeline.resolution[1],
        )

        update_task_step(task_id, "Rendering video", 92, "Mixing AAC audio track with beat alignment")

        output_filename = f"video_{task_id}.mp4"
        final_output_path = OUTPUT_DIR / output_filename

        if timeline.music_file and (UPLOAD_DIR / timeline.music_file).exists():
            music_path = UPLOAD_DIR / timeline.music_file
            mix_audio_track(
                video_path=raw_concat,
                music_path=music_path,
                out_final_path=final_output_path,
                video_duration=timeline.duration,
                music_volume=music_volume,
            )
        else:
            shutil.copy(str(raw_concat), str(final_output_path))

        update_task_step(task_id, "Rendering video", 100, "Completed rendering successfully!")

        result_url = f"/outputs/{output_filename}"

        task_data = {
            "task_id": task_id,
            "status": "completed",
            "progress": 100,
            "current_step": "Completed",
            "step_details": [
                {"name": "Analyzing media",       "status": "completed", "details": "Detected resolution and faces"},
                {"name": "Analyzing music",        "status": "completed", "details": "Extracted BPM and tempo"},
                {"name": "Detecting beats",        "status": "completed", "details": "Calculated beat timestamps"},
                {"name": "Selecting best moments", "status": "completed", "details": "Prioritized high-quality shots"},
                {"name": "Creating timeline",      "status": "completed", "details": f"Planned {len(timeline.segments)} segments"},
                {"name": "Applying effects",       "status": "completed", "details": "Ken Burns pan/zoom applied"},
                {"name": "Adding transitions",     "status": "completed", "details": "Transitions merged"},
                {"name": "Rendering video",        "status": "completed", "details": "1080p MP4 ready"},
            ],
            "result_video_url": result_url,
            "timeline": timeline.model_dump(),
            "error": None,
        }
        save_task_progress(task_id, task_data)
        return result_url

    except Exception as e:
        print(f"[renderer] Rendering failed for task {task_id}: {e}")
        save_task_progress(task_id, {
            "task_id": task_id,
            "status": "failed",
            "progress": 0,
            "current_step": "Error",
            "step_details": [],
            "error": str(e),
        })
        raise e

    finally:
        if work_dir.exists():
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass
