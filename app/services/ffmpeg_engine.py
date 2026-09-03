import os
import subprocess
import shutil
import json
import numpy as np
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from typing import List, Dict, Any, Optional
from app.config import FFMPEG_PATH, FFPROBE_PATH, TEMP_DIR, UPLOAD_DIR
from app.models.schemas import Timeline, TimelineSegment

COLOR_FILTER_FFMPEG = {
    "cinematic": "eq=contrast=1.15:saturation=1.2:gamma=0.95",
    "vintage": "eq=contrast=1.05:saturation=0.7:gamma=1.1,colorbalance=rs=0.1:gs=-0.05:bs=-0.1",
    "vivid": "eq=contrast=1.2:saturation=1.5:brightness=0.02",
    "noir": "hue=s=0,eq=contrast=1.3:brightness=-0.02",
    "cyberpunk": "colorbalance=rs=0.2:gs=-0.1:bs=0.3,eq=contrast=1.2:saturation=1.4",
    "none": ""
}

def prepare_segment_clip(
    segment: TimelineSegment,
    target_res: List[int],
    out_dir: Path,
    index: int
) -> Path:
    """
    Normalizes a photo or video segment into a standalone MP4 clip matching target resolution.
    Applies Ken Burns photo motion effects, color grading filters, text overlays, and aspect-ratio adaptation.
    """
    width, height = target_res[0], target_res[1]
    input_path = UPLOAD_DIR / segment.file
    out_clip_path = out_dir / f"clip_{index:03d}_{segment.id}.mp4"
    fps = 30
    duration = max(1.5, segment.duration)
    filter_style = getattr(segment, 'color_filter', 'none') or 'none'

    if segment.type == "photo":
        create_photo_motion_clip(
            img_path=input_path,
            out_clip_path=out_clip_path,
            effect=segment.effect,
            duration=duration,
            target_width=width,
            target_height=height,
            fps=fps,
            text_overlay=segment.text_overlay,
            text_position=segment.text_position or "center",
            color_filter=filter_style
        )
    else:
        normalize_video_clip(
            video_path=input_path,
            out_clip_path=out_clip_path,
            trim_start=segment.trim_start,
            duration=duration,
            target_width=width,
            target_height=height,
            fps=fps,
            text_overlay=segment.text_overlay,
            text_position=segment.text_position or "center",
            color_filter=filter_style
        )

    return out_clip_path


def create_photo_motion_clip(
    img_path: Path,
    out_clip_path: Path,
    effect: str,
    duration: float,
    target_width: int,
    target_height: int,
    fps: int = 30,
    text_overlay: Optional[str] = None,
    text_position: str = "center",
    color_filter: str = "none"
):
    """
    Creates a pan/zoom motion clip from an image file using Pillow backdrop & FFmpeg zoompan filter.
    """
    prep_img_path = out_clip_path.parent / f"prep_{out_clip_path.stem}.png"
    fit_image_to_canvas(
        src_path=img_path,
        dst_path=prep_img_path,
        canvas_w=target_width,
        canvas_h=target_height,
        text_overlay=text_overlay,
        text_position=text_position
    )

    frames = int(duration * fps)

    zoom_expr = "min(zoom+0.0015,1.25)"
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"

    if effect == "zoom_out":
        zoom_expr = "max(1.25-0.0015*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == "pan_left":
        zoom_expr = "1.15"
        x_expr = "(1-on/d)*(iw-iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == "pan_right":
        zoom_expr = "1.15"
        x_expr = "(on/d)*(iw-iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == "pan_up":
        zoom_expr = "1.15"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "(1-on/d)*(ih-ih/zoom)"
    elif effect == "pan_down":
        zoom_expr = "1.15"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "(on/d)*(ih-ih/zoom)"

    filter_chain = f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={target_width}x{target_height}:fps={fps},format=yuv420p"
    
    # Add color filter if specified
    if color_filter in COLOR_FILTER_FFMPEG and COLOR_FILTER_FFMPEG[color_filter]:
        filter_chain += f",{COLOR_FILTER_FFMPEG[color_filter]}"

    cmd = [
        FFMPEG_PATH, "-y",
        "-loop", "1",
        "-i", str(prep_img_path),
        "-vf", filter_chain,
        "-c:v", "libx264",
        "-preset", "fast",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        str(out_clip_path)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        fallback_chain = f"scale={target_width}:{target_height},fps={fps},format=yuv420p"
        if color_filter in COLOR_FILTER_FFMPEG and COLOR_FILTER_FFMPEG[color_filter]:
            fallback_chain += f",{COLOR_FILTER_FFMPEG[color_filter]}"

        fallback_cmd = [
            FFMPEG_PATH, "-y",
            "-loop", "1",
            "-i", str(prep_img_path),
            "-vf", fallback_chain,
            "-c:v", "libx264",
            "-preset", "fast",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(out_clip_path)
        ]
        subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    if prep_img_path.exists():
        try:
            prep_img_path.unlink()
        except Exception:
            pass


def normalize_video_clip(
    video_path: Path,
    out_clip_path: Path,
    trim_start: float,
    duration: float,
    target_width: int,
    target_height: int,
    fps: int = 30,
    text_overlay: Optional[str] = None,
    text_position: str = "center",
    color_filter: str = "none"
):
    """
    Trims, scales, pads, color grades, and normalizes a video clip to target resolution.
    """
    filter_chain = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},format=yuv420p"
    )

    if color_filter in COLOR_FILTER_FFMPEG and COLOR_FILTER_FFMPEG[color_filter]:
        filter_chain += f",{COLOR_FILTER_FFMPEG[color_filter]}"

    cmd = [
        FFMPEG_PATH, "-y",
        "-ss", str(trim_start),
        "-i", str(video_path),
        "-vf", filter_chain,
        "-c:v", "libx264",
        "-preset", "fast",
        "-t", str(duration),
        "-an",
        "-pix_fmt", "yuv420p",
        str(out_clip_path)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error normalizing video clip {video_path}: {e.stderr.decode('utf-8', errors='ignore')}")
        raise e


def fit_image_to_canvas(
    src_path: Path,
    dst_path: Path,
    canvas_w: int,
    canvas_h: int,
    text_overlay: Optional[str] = None,
    text_position: str = "center"
):
    """
    Fits image into canvas with blurred background fill and optional text overlay drawn with Pillow.
    """
    with Image.open(src_path) as orig_img:
        orig_img = orig_img.convert("RGB")
        w, h = orig_img.size

        # Create blurred background
        bg = orig_img.resize((canvas_w, canvas_h), Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=25))

        # Scale main image preserving aspect ratio
        ratio = min(canvas_w / w, canvas_h / h)
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        resized_fg = orig_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Paste centered
        paste_x = (canvas_w - new_w) // 2
        paste_y = (canvas_h - new_h) // 2
        bg.paste(resized_fg, (paste_x, paste_y))

        # Render text overlay if present
        if text_overlay:
            draw = ImageDraw.Draw(bg, "RGBA")
            font_size = max(24, int(canvas_h * 0.045))
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

            left, top, right, bottom = draw.textbbox((0, 0), text_overlay, font=font)
            text_w = right - left
            text_h = bottom - top

            text_x = (canvas_w - text_w) // 2
            if text_position == "top":
                text_y = int(canvas_h * 0.1)
            elif text_position in ["bottom", "lower_third"]:
                text_y = int(canvas_h * 0.8)
            else:
                text_y = (canvas_h - text_h) // 2

            padding = 16
            pill_rect = [
                text_x - padding,
                text_y - padding // 2,
                text_x + text_w + padding,
                text_y + text_h + padding // 2
            ]
            draw.rectangle(pill_rect, fill=(0, 0, 0, 170))
            draw.text((text_x, text_y), text_overlay, fill=(255, 255, 255, 255), font=font)

        bg.save(dst_path, "PNG")


def concatenate_clips_with_transitions(
    clip_paths: List[Path],
    transitions: List[str],
    durations: List[float],
    out_video_path: Path,
    target_width: int,
    target_height: int
):
    """
    Concatenates multiple normalized clips using FFmpeg concat protocol.
    """
    if len(clip_paths) == 1:
        shutil.copy(str(clip_paths[0]), str(out_video_path))
        return

    concat_list_file = out_video_path.parent / "concat_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    cmd = [
        FFMPEG_PATH, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c:v", "libx264",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        str(out_video_path)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    finally:
        if concat_list_file.exists():
            try:
                concat_list_file.unlink()
            except Exception:
                pass


def mix_audio_track(
    video_path: Path,
    music_path: Path,
    out_final_path: Path,
    video_duration: float,
    music_volume: float = 0.8
):
    """
    Mixes music track with final video, loops/trims audio to match video duration, applies volume level & fade-out.
    """
    fade_start = max(0.0, video_duration - 1.5)
    audio_filter = f"volume={music_volume},afade=t=in:ss=0:d=1,afade=t=out:st={fade_start}:d=1.5"

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(music_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", audio_filter,
        "-shortest",
        "-t", str(video_duration),
        str(out_final_path)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Audio mix error, copying video as fallback: {e.stderr.decode('utf-8', errors='ignore')}")
        shutil.copy(str(video_path), str(out_final_path))
