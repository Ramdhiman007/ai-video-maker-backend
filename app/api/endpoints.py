import os
import uuid
import shutil
import asyncio
import tempfile
from pathlib import Path
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
import subprocess

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Request
from app.config import (
    UPLOAD_DIR, OUTPUT_DIR, FFMPEG_PATH,
    USE_R2, MAX_UPLOAD_SIZE_BYTES, TEMP_DIR
)
from app.models.schemas import (
    MediaItem, UploadResponse, CreateVideoRequest,
    VideoSettings, TaskProgress, Timeline
)
from app.utils.redis_store import (
    save_media_metadata, get_media_metadata,
    save_task_progress, get_task_progress, update_task_step
)
from app.utils.r2_storage import (
    upload_media_file, upload_output_video,
    download_file, is_r2_configured
)
from app.services.media_analyzer import analyze_photo, analyze_video
from app.services.audio_analyzer import analyze_audio
from app.services.ai_planner import plan_timeline
from app.services.video_renderer import render_video_from_timeline

router = APIRouter()

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm"}
SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".m4a"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _save_upload_locally(file: UploadFile, dest: Path):
    """Streams UploadFile to disk, enforcing MAX_UPLOAD_SIZE_BYTES."""
    total = 0
    with open(dest, "wb") as buf:
        while chunk := file.file.read(1024 * 256):
            total += len(chunk)
            if total > MAX_UPLOAD_SIZE_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max size is {MAX_UPLOAD_SIZE_BYTES // (1024*1024)} MB."
                )
            buf.write(chunk)


def _media_url(r2_url: Optional[str], local_filename: str) -> str:
    """Returns R2 public URL in production, or local relative path in dev."""
    if r2_url:
        return r2_url
    return f"/uploads/{local_filename}"


# ─── Upload ───────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse)
async def upload_media(files: List[UploadFile] = File(...)):
    """
    Uploads photo/video/audio files.
    In production: saves to Cloudflare R2.
    In dev: saves locally.
    """
    uploaded_items: List[MediaItem] = []
    job_id = uuid.uuid4().hex[:12]

    for file in files:
        if not file.filename:
            continue

        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in (SUPPORTED_IMAGE_EXTS | SUPPORTED_VIDEO_EXTS | SUPPORTED_AUDIO_EXTS):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: '{file_ext}'. Allowed: JPG, PNG, WEBP, MP4, MOV, AVI, WEBM, MP3, WAV, M4A."
            )

        media_id = f"med_{uuid.uuid4().hex[:8]}"
        safe_name = "".join(c for c in Path(file.filename).stem if c.isalnum() or c in "-_")[:40]
        saved_filename = f"{media_id}{file_ext}"
        local_path = UPLOAD_DIR / saved_filename

        # Save to local disk first (needed for analysis)
        _save_upload_locally(file, local_path)
        file_size = local_path.stat().st_size

        r2_url: Optional[str] = None
        r2_key: Optional[str] = None

        # Upload to R2 if configured
        if is_r2_configured():
            try:
                r2_key, r2_url = upload_media_file(local_path, job_id, saved_filename)
            except Exception as e:
                print(f"[upload] R2 upload failed for {file.filename}: {e} — using local fallback")

        if file_ext in SUPPORTED_IMAGE_EXTS:
            analysis = analyze_photo(local_path)
            thumb_url = _media_url(r2_url, analysis.get("thumbnail", saved_filename))
            item = MediaItem(
                id=media_id,
                filename=saved_filename,
                original_name=file.filename,
                media_type="photo",
                mime_type=f"image/{file_ext.lstrip('.')}",
                size=file_size,
                url=thumb_url,
                r2_key=r2_key,
                width=analysis.get("width"),
                height=analysis.get("height"),
                orientation=analysis.get("orientation"),
                faces_count=analysis.get("faces_count", 0),
                face_boxes=analysis.get("face_boxes", []),
                quality_score=analysis.get("quality_score", 0.8),
            )

        elif file_ext in SUPPORTED_VIDEO_EXTS:
            analysis = analyze_video(local_path)
            thumb_url = _media_url(r2_url, analysis.get("thumbnail", saved_filename))
            item = MediaItem(
                id=media_id,
                filename=saved_filename,
                original_name=file.filename,
                media_type="video",
                mime_type=f"video/{file_ext.lstrip('.')}",
                size=file_size,
                url=thumb_url,
                r2_key=r2_key,
                width=analysis.get("width"),
                height=analysis.get("height"),
                duration=analysis.get("duration"),
                orientation=analysis.get("orientation"),
                quality_score=analysis.get("quality_score", 0.85),
            )

        else:  # audio
            analysis = analyze_audio(local_path)
            item = MediaItem(
                id=media_id,
                filename=saved_filename,
                original_name=file.filename,
                media_type="audio",
                mime_type=f"audio/{file_ext.lstrip('.')}",
                size=file_size,
                url=_media_url(r2_url, saved_filename),
                r2_key=r2_key,
                duration=analysis.get("duration"),
                bpm=analysis.get("bpm"),
                beats=analysis.get("beats", []),
            )

        save_media_metadata(media_id, item.model_dump())
        uploaded_items.append(item)

    if not uploaded_items:
        raise HTTPException(status_code=400, detail="No valid files were uploaded.")

    return UploadResponse(files=uploaded_items)


# ─── Load Demo Assets ─────────────────────────────────────────────────────────

@router.post("/load-demo-assets", response_model=UploadResponse)
async def load_demo_assets():
    """Generates synthetic demo photos, video clip, and audio beat track for 1-click testing."""
    demo_items: List[MediaItem] = []

    def _make_demo_photo(color, shapes_fn, name) -> MediaItem:
        mid = f"demo_{uuid.uuid4().hex[:6]}"
        fn = f"{mid}.png"
        p = UPLOAD_DIR / fn
        img = Image.new("RGB", (1920, 1080), color=color)
        shapes_fn(ImageDraw.Draw(img))
        img.save(p)
        a = analyze_photo(p)
        r2_url = None
        r2_key = None
        if is_r2_configured():
            try:
                r2_key, r2_url = upload_media_file(p, "demo", fn)
            except Exception:
                pass
        item = MediaItem(
            id=mid, filename=fn, original_name=name, media_type="photo",
            mime_type="image/png", size=p.stat().st_size,
            url=r2_url or f"/uploads/{a.get('thumbnail', fn)}",
            r2_key=r2_key, width=1920, height=1080,
            orientation="landscape", faces_count=0, quality_score=0.95,
        )
        save_media_metadata(mid, item.model_dump())
        return item

    demo_items.append(_make_demo_photo(
        (255, 120, 80),
        lambda d: (d.rectangle([0, 500, 1920, 1080], fill=(20, 80, 160)),
                   d.ellipse([800, 300, 1100, 600], fill=(255, 230, 150))),
        "Sunset Horizon.png",
    ))
    demo_items.append(_make_demo_photo(
        (40, 140, 100),
        lambda d: d.polygon([(540, 400), (100, 1080), (980, 1080)], fill=(80, 90, 110)),
        "Alpine Peak.png",
    ))
    demo_items.append(_make_demo_photo(
        (15, 20, 45),
        lambda d: (d.rectangle([200, 300, 400, 1080], fill=(240, 180, 50)),
                   d.rectangle([500, 200, 750, 1080], fill=(180, 50, 240)),
                   d.rectangle([850, 400, 1200, 1080], fill=(50, 200, 240))),
        "Neon Skyline.png",
    ))

    # Demo video clip
    id4 = f"demo_{uuid.uuid4().hex[:6]}"
    fn4 = f"{id4}.mp4"
    p4 = UPLOAD_DIR / fn4
    subprocess.run(
        [FFMPEG_PATH, "-y", "-f", "lavfi", "-i", "testsrc=duration=6:size=1280x720:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p4)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    a4 = analyze_video(p4)
    r2_url4 = r2_key4 = None
    if is_r2_configured():
        try:
            r2_key4, r2_url4 = upload_media_file(p4, "demo", fn4)
        except Exception:
            pass
    item4 = MediaItem(
        id=id4, filename=fn4, original_name="Motion Clip.mp4", media_type="video",
        mime_type="video/mp4", size=p4.stat().st_size,
        url=r2_url4 or f"/uploads/{a4.get('thumbnail', fn4)}",
        r2_key=r2_key4, width=1280, height=720, duration=6.0,
        orientation="landscape", quality_score=0.88,
    )
    save_media_metadata(id4, item4.model_dump())
    demo_items.append(item4)

    # Demo audio beat track
    id5 = f"demo_{uuid.uuid4().hex[:6]}"
    fn5 = f"{id5}.wav"
    p5 = UPLOAD_DIR / fn5
    subprocess.run(
        [FFMPEG_PATH, "-y", "-f", "lavfi", "-i", "sine=frequency=523.25:duration=15",
         "-c:a", "pcm_s16le", str(p5)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    a5 = analyze_audio(p5)
    r2_url5 = r2_key5 = None
    if is_r2_configured():
        try:
            r2_key5, r2_url5 = upload_media_file(p5, "demo", fn5)
        except Exception:
            pass
    item5 = MediaItem(
        id=id5, filename=fn5, original_name="Upbeat Beat Track.wav", media_type="audio",
        mime_type="audio/wav", size=p5.stat().st_size,
        url=r2_url5 or f"/uploads/{fn5}",
        r2_key=r2_key5, duration=15.0, bpm=120.0, beats=a5.get("beats", []),
    )
    save_media_metadata(id5, item5.model_dump())
    demo_items.append(item5)

    return UploadResponse(files=demo_items)


# ─── Create Video ─────────────────────────────────────────────────────────────

def run_video_generation_task(task_id: str, media_ids: List[str], settings: VideoSettings):
    """
    Background task: full AI video creation pipeline.
    Downloads media from R2 if needed, renders video, uploads result to R2.
    """
    work_dir = TEMP_DIR / f"render_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        update_task_step(task_id, "Analyzing media", 10, "Extracting resolution, faces, and orientation")
        media_items = [get_media_metadata(m_id) for m_id in media_ids if get_media_metadata(m_id)]

        if not media_items:
            raise ValueError("No valid media files found for video creation.")

        # In production: ensure files are available locally for FFmpeg processing
        if is_r2_configured():
            update_task_step(task_id, "Downloading media", 15, "Fetching files from cloud storage")
            for item in media_items:
                r2_key = item.get("r2_key")
                local_path = UPLOAD_DIR / item["filename"]
                if r2_key and not local_path.exists():
                    try:
                        download_file(r2_key, local_path)
                    except Exception as e:
                        print(f"[task] Failed to download {r2_key}: {e}")

        update_task_step(task_id, "Analyzing music", 20, "Extracting tempo, BPM, and beat timestamps")
        audio_items = [m for m in media_items if m["media_type"] == "audio"]
        music_item = audio_items[0] if audio_items else None

        update_task_step(task_id, "Detecting beats", 30, "Mapping beat sync timing arrays")

        timeline = plan_timeline(media_items, settings, music_item)
        result_url = render_video_from_timeline(task_id, timeline, settings.music_volume)

        # Upload final video to R2 if configured
        if is_r2_configured():
            output_filename = f"video_{task_id}.mp4"
            local_output = OUTPUT_DIR / output_filename
            if local_output.exists():
                try:
                    r2_result_url = upload_output_video(local_output, task_id)
                    # Update task with R2 URL
                    task = get_task_progress(task_id) or {}
                    task["result_video_url"] = r2_result_url
                    save_task_progress(task_id, task)
                    print(f"[task] Uploaded final video to R2: {r2_result_url}")
                except Exception as e:
                    print(f"[task] R2 video upload failed: {e} — keeping local URL")

    except Exception as e:
        print(f"[task] Video task {task_id} failed: {e}")
        save_task_progress(task_id, {
            "task_id": task_id,
            "status": "failed",
            "progress": 0,
            "current_step": "Failed",
            "step_details": [],
            "error": str(e),
        })
    finally:
        # Clean up worker temp directory
        if work_dir.exists():
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass


@router.post("/create-video", response_model=TaskProgress)
async def create_video(request: CreateVideoRequest, background_tasks: BackgroundTasks):
    """Validates request and starts background video generation task."""
    if not request.media_ids:
        raise HTTPException(status_code=400, detail="At least one media item must be provided.")

    task_id = f"task_{uuid.uuid4().hex[:8]}"

    initial_task = TaskProgress(
        task_id=task_id,
        status="processing",
        progress=5,
        current_step="Analyzing media",
        step_details=[
            {"name": "Analyzing media",        "status": "in_progress", "details": "Extracting features"},
            {"name": "Analyzing music",         "status": "pending",     "details": "BPM & beat detection"},
            {"name": "Detecting beats",         "status": "pending",     "details": "Syncing cut timings"},
            {"name": "Selecting best moments",  "status": "pending",     "details": "Prioritizing quality shots"},
            {"name": "Creating timeline",       "status": "pending",     "details": "AI timeline planning"},
            {"name": "Applying effects",        "status": "pending",     "details": "Ken Burns pan/zoom"},
            {"name": "Adding transitions",      "status": "pending",     "details": "Crossfades & cuts"},
            {"name": "Rendering video",         "status": "pending",     "details": "FFmpeg H.264 rendering"},
        ],
    )
    save_task_progress(task_id, initial_task.model_dump())
    background_tasks.add_task(run_video_generation_task, task_id, request.media_ids, request.settings)
    return initial_task


# ─── Status ───────────────────────────────────────────────────────────────────

@router.get("/status/{task_id}", response_model=TaskProgress)
async def get_status(task_id: str):
    """Returns real-time task progress from Redis (or in-memory fallback)."""
    progress = get_task_progress(task_id)
    if not progress:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return TaskProgress(**progress)


# ─── Result ───────────────────────────────────────────────────────────────────

@router.get("/result/{task_id}")
async def get_result(task_id: str):
    """Returns the final video URL for a completed task."""
    progress = get_task_progress(task_id)
    if not progress:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    if progress.get("status") != "completed":
        raise HTTPException(status_code=425, detail="Video is not yet ready.")
    return {
        "task_id": task_id,
        "status": "completed",
        "result_video_url": progress.get("result_video_url"),
    }


# ─── Regenerate ───────────────────────────────────────────────────────────────

@router.post("/regenerate/{task_id}", response_model=TaskProgress)
async def regenerate_video(task_id: str, background_tasks: BackgroundTasks):
    """Regenerates a new video variation without re-uploading media."""
    prev_task = get_task_progress(task_id)
    if not prev_task or not prev_task.get("timeline"):
        raise HTTPException(status_code=404, detail="Original task or timeline not found.")

    new_task_id = f"task_{uuid.uuid4().hex[:8]}"
    prev_timeline = prev_task["timeline"]

    settings = VideoSettings(
        title=prev_timeline.get("title", "Regenerated Video"),
        template=prev_timeline.get("template", "auto"),
        aspect_ratio=prev_timeline.get("aspect_ratio", "16:9"),
        quality="1080p",
        target_duration="unlimited",
        enable_text=True,
        enable_transitions=True,
    )

    media_ids = [
        seg["media_id"]
        for seg in prev_timeline.get("segments", [])
        if "media_id" in seg
    ]

    initial_task = TaskProgress(
        task_id=new_task_id,
        status="processing",
        progress=5,
        current_step="Regenerating timeline",
        step_details=[
            {"name": "Analyzing media",        "status": "completed",   "details": "Re-using cached media"},
            {"name": "Analyzing music",         "status": "completed",   "details": "Re-using music beats"},
            {"name": "Detecting beats",         "status": "completed",   "details": "Re-using sync array"},
            {"name": "Selecting best moments",  "status": "in_progress", "details": "Randomizing shot order"},
            {"name": "Creating timeline",       "status": "pending",     "details": "Varying motion effects"},
            {"name": "Applying effects",        "status": "pending",     "details": "Re-rendering video"},
            {"name": "Adding transitions",      "status": "pending",     "details": "Varying transition styles"},
            {"name": "Rendering video",         "status": "pending",     "details": "Rendering MP4"},
        ],
    )
    save_task_progress(new_task_id, initial_task.model_dump())
    background_tasks.add_task(run_video_generation_task, new_task_id, media_ids, settings)
    return initial_task


# ─── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/video/{task_id}")
async def delete_video(task_id: str):
    """Deletes task state. R2 files expire automatically via lifecycle rules."""
    task = get_task_progress(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    save_task_progress(task_id, {})
    return {"message": f"Task {task_id} deleted."}


# ─── Debug Tasks ─────────────────────────────────────────────────────────────

@router.get("/debug-tasks")
def debug_tasks():
    from app.utils.redis_store import _DB_PATH
    import sqlite3
    rows = []
    try:
        with sqlite3.connect(_DB_PATH, timeout=5.0) as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, val FROM store WHERE key LIKE 'task:%' ORDER BY rowid DESC LIMIT 10")
            for k, v in cur.fetchall():
                try:
                    rows.append({"key": k, "val": json.loads(v)})
                except Exception:
                    rows.append({"key": k, "val": v})
    except Exception as e:
        return {"error": str(e)}
    return {"tasks": rows}
