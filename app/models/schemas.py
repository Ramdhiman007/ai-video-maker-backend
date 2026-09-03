from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class MediaItem(BaseModel):
    id: str
    filename: str
    original_name: str
    media_type: str          # 'photo', 'video', 'audio'
    mime_type: str
    size: int
    url: str                 # Public URL (R2 in production, /uploads/... in dev)
    r2_key: Optional[str] = None  # Cloudflare R2 object key (production only)
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    orientation: Optional[str] = None   # 'landscape', 'portrait', 'square'
    faces_count: Optional[int] = 0
    face_boxes: Optional[List[List[int]]] = []   # [[x, y, w, h], ...]
    quality_score: Optional[float] = 1.0
    bpm: Optional[float] = None
    beats: Optional[List[float]] = []

class UploadResponse(BaseModel):
    files: List[MediaItem]

class VideoSettings(BaseModel):
    title: Optional[str] = "My AI Video"
    template: str = "auto"
    aspect_ratio: str = "16:9"          # '16:9', '9:16', '1:1', 'custom'
    quality: str = "1080p"              # '720p', '1080p', '4K', 'custom'
    custom_width: Optional[int] = None
    custom_height: Optional[int] = None
    target_size_mode: str = "standard"  # 'compressed', 'standard', 'high_quality'
    max_file_mb: Optional[int] = None
    target_duration: str = "unlimited"  # 'unlimited', 'auto', '15', '30', '60', '120'
    enable_text: bool = True
    enable_transitions: bool = True
    color_filter: str = "none"          # 'none', 'cinematic', 'vintage', 'vivid', 'noir', 'cyberpunk'
    music_volume: float = 0.8
    video_audio_volume: float = 0.2
    caption_style: str = "modern"       # 'modern', 'cinematic', 'neon', 'subtle'
    music_id: Optional[str] = None

class TimelineSegment(BaseModel):
    id: str
    type: str               # 'photo' or 'video'
    file: str
    media_id: str
    start_time: float
    duration: float
    trim_start: float = 0.0
    effect: str = "zoom_in"
    transition: str = "fade"
    text_overlay: Optional[str] = None
    text_position: Optional[str] = "center"
    color_filter: Optional[str] = "none"
    face_bboxes: Optional[List[List[int]]] = []

class Timeline(BaseModel):
    title: str
    template: str
    aspect_ratio: str
    resolution: List[int]   # [width, height]
    duration: float
    music_file: Optional[str] = None
    bpm: Optional[float] = None
    segments: List[TimelineSegment]

class CreateVideoRequest(BaseModel):
    media_ids: List[str]
    settings: VideoSettings

class TaskProgress(BaseModel):
    task_id: str
    status: str             # 'queued', 'processing', 'completed', 'failed'
    progress: int           # 0 to 100
    current_step: str
    step_details: List[Dict[str, Any]]
    result_video_url: Optional[str] = None
    timeline: Optional[Timeline] = None
    error: Optional[str] = None
