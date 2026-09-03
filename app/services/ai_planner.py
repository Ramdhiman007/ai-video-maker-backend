import os
import json
import random
import uuid
from typing import List, Dict, Any, Optional
from app.config import GEMINI_API_KEY
from app.models.schemas import VideoSettings, Timeline, TimelineSegment

EFFECTS_LIST = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "rotate"]
TRANSITIONS_LIST = ["fade", "dissolve", "zoom", "slideleft", "slideright", "push"]

TEMPLATE_TITLE_PRESETS = {
    "travel": ["Wanderlust Journey", "Exploring New Horizons", "Unforgettable Travel Moments", "Adventures Await"],
    "birthday": ["Happy Birthday Celebration!", "Cheers to Another Great Year", "Birthday Memories", "Special Day Moments"],
    "wedding": ["Our Love Story", "A Day to Remember Forever", "Eternal Bond & Bliss", "Together Forever"],
    "family": ["Family Memories", "Precious Moments Together", "Family Is Everything", "Good Times & Big Smiles"],
    "romantic": ["Forever & Always", "Love in Every Moment", "Sweet Memories", "Together With You"],
    "motivation": ["Unstoppable Energy", "Rise & Conquer", "The Journey to Greatness", "Dream Big Work Hard"],
    "youtube": ["Welcome to My Channel", "Best Highlights & Moments", "Daily Vlog Highlights", "Watch Till the End!"],
    "shorts": ["Quick Highlights ⚡", "Top Moments You Need to See", "Viral Short Blast 🔥", "Must Watch!"],
    "reels": ["Aesthetic Vibe ✨", "Unfiltered Moments", "Weekend Energy 🔥", "Captured Stories"],
    "festival": ["Festival Magic & Lights", "Celebration Vibes", "Festival Season Memories", "Joy & Celebrations"],
    "product": ["Introducing the Next Big Thing", "Designed for Excellence", "Elevate Your Lifestyle", "Premium Experience"],
    "cinematic": ["Cinematic Odyssey", "A Visual Masterpiece", "Beyond the Horizon", "Reflections & Light"]
}

def get_target_resolution(settings: VideoSettings) -> List[int]:
    """Calculates target video width and height from settings or custom values."""
    if settings.custom_width and settings.custom_height and settings.custom_width > 0 and settings.custom_height > 0:
        # Ensure dimensions are even numbers for H.264 encoder compatibility
        w = settings.custom_width if settings.custom_width % 2 == 0 else settings.custom_width + 1
        h = settings.custom_height if settings.custom_height % 2 == 0 else settings.custom_height + 1
        return [w, h]

    resolution_map = {
        "16:9": {"720p": [1280, 720], "1080p": [1920, 1080], "4K": [3840, 2160]},
        "9:16": {"720p": [720, 1280], "1080p": [1080, 1920], "4K": [2160, 3840]},
        "1:1":  {"720p": [720, 720],   "1080p": [1080, 1080], "4K": [2160, 2160]}
    }
    return resolution_map.get(settings.aspect_ratio, {}).get(settings.quality, [1920, 1080])

def plan_timeline(
    media_items: List[Dict[str, Any]],
    settings: VideoSettings,
    music_item: Optional[Dict[str, Any]] = None
) -> Timeline:
    """
    Creates a structured video timeline. Uses Gemini LLM API if key is present,
    otherwise falls back to algorithmic AI template planner.
    """
    if GEMINI_API_KEY:
        try:
            return plan_timeline_with_gemini(media_items, settings, music_item)
        except Exception as e:
            print(f"Gemini API planning failed, switching to algorithmic fallback: {e}")

    return plan_timeline_algorithmic(media_items, settings, music_item)


def plan_timeline_algorithmic(
    media_items: List[Dict[str, Any]],
    settings: VideoSettings,
    music_item: Optional[Dict[str, Any]] = None
) -> Timeline:
    """
    Intelligent algorithmic fallback planner. Supports UNLIMITED duration & Custom Video Sizes.
    """
    photos_and_videos = [m for m in media_items if m["media_type"] in ["photo", "video"]]
    if not photos_and_videos:
        raise ValueError("No photos or videos provided to plan timeline.")

    photos_and_videos.sort(key=lambda x: (x.get("faces_count", 0) > 0, x.get("quality_score", 0.5)), reverse=True)

    resolution = get_target_resolution(settings)

    beats = music_item.get("beats", []) if music_item else []
    bpm = music_item.get("bpm", 120.0) if music_item else 120.0
    music_duration = music_item.get("duration", 30.0) if music_item else 30.0

    item_count = len(photos_and_videos)
    # Estimate natural duration: 3.5s per photo, up to 6s per video clip
    natural_durations = [
        min(6.0, max(2.5, m.get("duration", 4.0))) if m["media_type"] == "video" else 3.5
        for m in photos_and_videos
    ]
    total_natural_dur = sum(natural_durations)

    if settings.target_duration == "15":
        target_total_dur = 15.0
    elif settings.target_duration == "30":
        target_total_dur = 30.0
    elif settings.target_duration == "60":
        target_total_dur = 60.0
    elif settings.target_duration == "90":
        target_total_dur = 90.0
    elif settings.target_duration == "120":
        target_total_dur = 120.0
    else:  # "auto" or "unlimited" (default)
        # Smart adaptive duration: show each photo/video naturally without excessive duplicate looping
        if item_count == 1:
            target_total_dur = max(8.0, total_natural_dur * 2)
        elif item_count <= 3:
            target_total_dur = max(10.0, total_natural_dur)
        elif item_count <= 8:
            target_total_dur = min(35.0, total_natural_dur)
        else:
            target_total_dur = min(60.0, total_natural_dur)

        if music_item and music_duration > 0:
            target_total_dur = min(target_total_dur, music_duration)

    beat_step = 4 if bpm >= 110 else 2
    beat_times = []
    if len(beats) > beat_step:
        beat_times = beats[::beat_step]

    segments: List[TimelineSegment] = []
    current_time = 0.0
    media_idx = 0

    preset_titles = TEMPLATE_TITLE_PRESETS.get(settings.template, TEMPLATE_TITLE_PRESETS["travel"])
    chosen_title = settings.title if settings.title and settings.title != "My AI Video" else random.choice(preset_titles)

    # Cap maximum segments to prevent memory overload or excessive render time
    max_loops = min(16, max(item_count * 2, int(target_total_dur / 2.5)))

    while current_time < target_total_dur and len(segments) < max_loops:
        media_item = photos_and_videos[media_idx % item_count]
        media_idx += 1

        if beat_times:
            next_beats = [b for b in beat_times if b >= current_time + 2.0]
            if next_beats:
                seg_duration = round(next_beats[0] - current_time, 2)
            else:
                seg_duration = 3.5
        else:
            seg_duration = 3.5 if media_item["media_type"] == "photo" else min(6.0, media_item.get("duration", 4.0))

        seg_duration = max(2.0, min(8.0, seg_duration))
        if current_time + seg_duration > target_total_dur + 1.0:
            seg_duration = max(1.5, target_total_dur - current_time)

        effect = random.choice(EFFECTS_LIST) if media_item["media_type"] == "photo" else "none"
        transition = random.choice(TRANSITIONS_LIST) if settings.enable_transitions else "none"
        if len(segments) == 0:
            transition = "fade"

        text_overlay = None
        text_position = "center"
        if settings.enable_text:
            if len(segments) == 0:
                text_overlay = chosen_title
                text_position = "center"
            elif len(segments) == item_count - 1:
                text_overlay = "Thanks for Watching"
                text_position = "lower_third"

        segment = TimelineSegment(
            id=f"seg_{len(segments) + 1}_{uuid.uuid4().hex[:4]}",
            type=media_item["media_type"],
            file=media_item["filename"],
            media_id=media_item["id"],
            start_time=round(current_time, 2),
            duration=round(seg_duration, 2),
            trim_start=0.0,
            effect=effect,
            transition=transition,
            text_overlay=text_overlay,
            text_position=text_position,
            face_bboxes=media_item.get("face_boxes", [])
        )
        segments.append(segment)
        current_time += seg_duration

        if settings.target_duration == "unlimited" and media_idx >= item_count and current_time >= target_total_dur:
            break

    return Timeline(
        title=chosen_title,
        template=settings.template,
        aspect_ratio=settings.aspect_ratio,
        resolution=resolution,
        duration=round(current_time, 2),
        music_file=music_item["filename"] if music_item else None,
        bpm=bpm if music_item else None,
        segments=segments
    )


def plan_timeline_with_gemini(
    media_items: List[Dict[str, Any]],
    settings: VideoSettings,
    music_item: Optional[Dict[str, Any]] = None
) -> Timeline:
    """Invokes Google Gemini API to structure the video timeline."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    media_summary = []
    for item in media_items:
        media_summary.append({
            "id": item["id"],
            "filename": item["filename"],
            "type": item["media_type"],
            "duration": item.get("duration", 3.0),
            "orientation": item.get("orientation", "landscape"),
            "faces": item.get("faces_count", 0),
            "quality": item.get("quality_score", 0.8)
        })
        
    music_summary = {}
    if music_item:
        music_summary = {
            "filename": music_item["filename"],
            "duration": music_item.get("duration", 30.0),
            "bpm": music_item.get("bpm", 120.0),
            "beats_count": len(music_item.get("beats", []))
        }

    prompt = f"""
You are an expert AI Video Editor and Director.
Create an engaging video timeline JSON based on these media items and settings:

User Settings:
- Title: {settings.title}
- Template Style: {settings.template}
- Aspect Ratio: {settings.aspect_ratio}
- Quality: {settings.quality}
- Target Duration Mode: {settings.target_duration}
- Text Enabled: {settings.enable_text}
- Transitions Enabled: {settings.enable_transitions}

Uploaded Media:
{json.dumps(media_summary, indent=2)}

Audio Music:
{json.dumps(music_summary, indent=2)}

Return ONLY raw JSON matching this schema:
{{
  "title": "{settings.title}",
  "template": "{settings.template}",
  "aspect_ratio": "{settings.aspect_ratio}",
  "duration": 60.0,
  "segments": [
    {{
      "id": "seg_1",
      "type": "photo",
      "file": "filename.jpg",
      "media_id": "media_id",
      "start_time": 0.0,
      "duration": 3.5,
      "trim_start": 0.0,
      "effect": "zoom_in",
      "transition": "fade",
      "text_overlay": "Title",
      "text_position": "center"
    }}
  ]
}}
"""

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7
        )
    )

    timeline_data = json.loads(response.text)
    return parse_raw_timeline_json(timeline_data, media_items, settings, music_item)


def parse_raw_timeline_json(
    raw: Dict[str, Any],
    media_items: List[Dict[str, Any]],
    settings: VideoSettings,
    music_item: Optional[Dict[str, Any]]
) -> Timeline:
    """Converts raw LLM dict output into validated Pydantic Timeline."""
    resolution = get_target_resolution(settings)

    segments = []
    media_by_id = {m["id"]: m for m in media_items}
    media_by_name = {m["filename"]: m for m in media_items}

    for raw_seg in raw.get("segments", []):
        media_id = raw_seg.get("media_id")
        file_name = raw_seg.get("file")
        m_item = media_by_id.get(media_id) or media_by_name.get(file_name) or media_items[0]
        
        seg = TimelineSegment(
            id=raw_seg.get("id", f"seg_{len(segments)+1}"),
            type=m_item["media_type"],
            file=m_item["filename"],
            media_id=m_item["id"],
            start_time=float(raw_seg.get("start_time", 0.0)),
            duration=float(raw_seg.get("duration", 3.5)),
            trim_start=float(raw_seg.get("trim_start", 0.0)),
            effect=raw_seg.get("effect", "zoom_in"),
            transition=raw_seg.get("transition", "fade"),
            text_overlay=raw_seg.get("text_overlay"),
            text_position=raw_seg.get("text_position", "center"),
            face_bboxes=m_item.get("face_boxes", [])
        )
        segments.append(seg)

    return Timeline(
        title=raw.get("title", settings.title or "My AI Video"),
        template=settings.template,
        aspect_ratio=settings.aspect_ratio,
        resolution=resolution,
        duration=sum(s.duration for s in segments),
        music_file=music_item["filename"] if music_item else None,
        bpm=music_item.get("bpm", 120.0) if music_item else None,
        segments=segments
    )
