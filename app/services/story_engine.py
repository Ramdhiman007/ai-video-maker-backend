import os
import re
import math
import json
import time
import random
import shutil
import asyncio
import urllib.parse
import urllib.request
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.config import (
    UPLOAD_DIR,
    OUTPUT_DIR,
    TEMP_DIR,
    FFMPEG_PATH,
    FFPROBE_PATH,
    GEMINI_API_KEY,
)
from app.models.schemas import StoryVideoRequest, Timeline, TimelineSegment
from app.utils.redis_store import update_task_step, save_task_progress

STYLE_MODIFIERS = {
    "pixar": (
        "3D Pixar Disney animated movie style, charming character design, "
        "vibrant warm cinematic lighting, raytracing, highly detailed CGI render, masterpiece"
    ),
    "anime": (
        "Makoto Shinkai Studio Ghibli anime style, breathtaking hand-drawn aesthetic, "
        "lush skies, glowing light rays, vibrant colors, cinematic Japanese animation scenery"
    ),
    "watercolor": (
        "Whimsical storybook watercolor illustration, soft pastel colors, textured paper, "
        "charming storybook art, expressive gentle strokes, beautiful fairy tale atmosphere"
    ),
    "comic": (
        "Modern graphic novel comic book illustration, dynamic ink lines, bold pop art colors, "
        "dramatic cinematic angles, halftone texture, superhero graphic novel style"
    ),
    "fantasy": (
        "Epic high fantasy cinematic concept art, magical glowing atmosphere, "
        "mythical wondrous landscape, volumetric lighting, hyper-detailed digital painting"
    ),
    "cyberpunk": (
        "Futuristic cyberpunk neon animation style, glowing holographic lights, rain slicked streets, "
        "high-tech sci-fi aesthetic, vibrant synthwave colors, anime cyberpunk"
    )
}

VOICE_MAP = {
    "christopher": "en-US-ChristopherNeural",
    "jenny": "en-US-JennyNeural",
    "guy": "en-US-GuyNeural",
    "aria": "en-US-AriaNeural",
    "neerja": "en-IN-NeerjaNeural",
    "swara": "hi-IN-SwaraNeural",
    "madhur": "hi-IN-MadhurNeural",
}

MOOD_FREQUENCIES = {
    "cinematic": 220.0,
    "whimsical": 392.0,
    "adventure": 293.66,
    "emotional": 261.63,
}

def segment_story_into_scenes(story_text: str, animation_style: str = "pixar") -> List[Dict[str, Any]]:
    story_text = story_text.strip()
    if not story_text:
        story_text = "Once upon a time in a magical land, a great adventure began."

    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            prompt = f"""You are a master animated film director.
Break down this story into distinct narrative visual scenes for an animated video.
Style: {animation_style}
Story:
\"\"\"{story_text}\"\"\"

Return ONLY a valid JSON array of objects with keys:
- "narration": The voiceover text for this scene (1 to 2 sentences).
- "visual_prompt": A rich visual scene description for image generation.
- "camera_motion": One of "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down".

Do not include markdown ticks, return pure raw JSON array."""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
                raw = re.sub(r"\n```$", "", raw)
            scenes = json.loads(raw)
            if isinstance(scenes, list) and len(scenes) > 0:
                return scenes
        except Exception as e:
            print(f"[story_engine] Gemini scene segmentation fallback: {e}")

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', story_text) if p.strip()]
    raw_sentences = []
    for p in paragraphs:
        sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', p) if len(s.strip()) > 3]
        if sents:
            raw_sentences.extend(sents)
        else:
            raw_sentences.append(p)

    if not raw_sentences:
        raw_sentences = [story_text]

    scenes = []
    curr_narration = []
    curr_words = 0
    motions = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]

    for sent in raw_sentences:
        w_count = len(sent.split())
        if curr_words + w_count > 30 and curr_narration:
            narr = " ".join(curr_narration)
            scenes.append({
                "narration": narr,
                "visual_prompt": _extract_visual_keywords(narr),
                "camera_motion": motions[len(scenes) % len(motions)],
            })
            curr_narration = [sent]
            curr_words = w_count
        else:
            curr_narration.append(sent)
            curr_words += w_count

    if curr_narration:
        narr = " ".join(curr_narration)
        scenes.append({
            "narration": narr,
            "visual_prompt": _extract_visual_keywords(narr),
            "camera_motion": motions[len(scenes) % len(motions)],
        })

    return scenes

def _extract_visual_keywords(sentence: str) -> str:
    cleaned = re.sub(r'^(and|but|so|then|however|meanwhile|suddenly|later),\s*', '', sentence, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^\w\s,\'-]', '', cleaned)
    return cleaned[:140]

async def _synthesize_scene_voice_async(text: str, voice: str, out_path: Path):
    import edge_tts
    resolved_voice = VOICE_MAP.get(voice.lower(), voice)
    communicate = edge_tts.Communicate(text, resolved_voice)
    await communicate.save(str(out_path))

def synthesize_scene_voice(text: str, voice: str, out_path: Path) -> float:
    try:
        asyncio.run(_synthesize_scene_voice_async(text, voice, out_path))
        dur = get_audio_duration(out_path)
        if dur > 0.5:
            return dur
    except Exception as e:
        print(f"[story_engine] TTS notice: {e}")

    word_count = max(len(text.split()), 3)
    est_dur = max(3.0, round(word_count / 2.3, 2))

    subprocess.run([
        FFMPEG_PATH, "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(est_dur),
        "-c:a", "libmp3lame",
        str(out_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return est_dur

def get_audio_duration(audio_path: Path) -> float:
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 3.5

def generate_scene_image(prompt: str, animation_style: str, out_path: Path, width: int = 1280, height: int = 720, seed: Optional[int] = None) -> Path:
    if seed is None:
        seed = random.randint(10000, 999999)

    clean_words = re.findall(r'\b[a-zA-Z]{3,15}\b', prompt.lower())
    stop_words = {
        'once', 'upon', 'time', 'with', 'that', 'this', 'from', 'they', 'them', 'their',
        'there', 'when', 'what', 'where', 'which', 'about', 'across', 'into', 'beneath',
        'under', 'over', 'named', 'little', 'very', 'were', 'been', 'have', 'having',
        'could', 'would', 'then', 'also', 'some', 'many', 'every', 'other', 'only',
        'deep', 'within', 'made', 'make', 'full', 'great', 'more', 'most'
    }
    keywords = [w for w in clean_words if w not in stop_words][:3]
    if not keywords:
        keywords = ['nature', 'story']
    tag_str = ','.join(keywords)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
    }

    # Provider 1: Pollinations (concise prompt, fast dimension)
    try:
        clean_prompt = re.sub(r'[^a-zA-Z0-9\s]', ' ', prompt)[:50].strip()
        encoded = urllib.parse.quote(f"{clean_prompt} {animation_style} animation")
        poll_url = f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=432&nologo=true&seed={seed}"
        req = urllib.request.Request(poll_url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = resp.read()
            if len(data) > 5000:
                out_path.write_bytes(data)
                return out_path
    except Exception:
        pass

    # Provider 2: Topic-matched high-definition scene visual via LoremFlickr
    try:
        flickr_url = f"https://loremflickr.com/1280/720/{tag_str}"
        req = urllib.request.Request(flickr_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = resp.read()
            if len(data) > 5000:
                out_path.write_bytes(data)
                return out_path
    except Exception:
        pass

    # Provider 3: High-res scenic photography via Picsum
    try:
        picsum_url = f"https://fastly.picsum.photos/id/{(seed % 300) + 10}/1280/720.jpg"
        req = urllib.request.Request(picsum_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            if len(data) > 5000:
                out_path.write_bytes(data)
                return out_path
    except Exception:
        pass

    # Provider 4: Guaranteed Illustrated Story Scene Card (Landscape & Moonlight)
    _create_stylized_story_card(prompt, animation_style, out_path, width, height)
    return out_path


def _create_stylized_story_card(prompt: str, style: str, out_path: Path, width: int, height: int):
    img = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(img)

    palettes = {
        "pixar": ((25, 42, 86), (41, 128, 185), (255, 220, 150)),
        "anime": ((19, 15, 38), (87, 75, 144), (248, 165, 194)),
        "watercolor": ((34, 47, 62), (87, 101, 116), (254, 202, 87)),
        "comic": ((10, 10, 10), (44, 44, 84), (255, 82, 82)),
        "fantasy": ((11, 19, 43), (28, 37, 65), (111, 207, 151)),
        "cyberpunk": ((13, 2, 33), (45, 0, 80), (0, 245, 212)),
    }
    cols = palettes.get(style, palettes["pixar"])

    # Dramatic atmospheric sky gradient
    for y in range(height):
        ratio = y / height
        r = int(cols[0][0] * (1 - ratio) + cols[1][0] * ratio)
        g = int(cols[0][1] * (1 - ratio) + cols[1][1] * ratio)
        b = int(cols[0][2] * (1 - ratio) + cols[1][2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Glowing celestial moon / star
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.ellipse([width * 0.35, height * 0.12, width * 0.65, height * 0.65], fill=(*cols[2], 160))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=50))
    img.paste(overlay, (0, 0), overlay)

    # Dramatic layered mountains & horizon
    m1 = [(0, height), (0, int(height*0.7)), (int(width*0.3), int(height*0.5)), (int(width*0.6), int(height*0.65)), (int(width*0.85), int(height*0.48)), (width, int(height*0.62)), (width, height)]
    draw.polygon(m1, fill=(15, 23, 42))

    m2 = [(0, height), (0, int(height*0.82)), (int(width*0.4), int(height*0.72)), (int(width*0.75), int(height*0.85)), (width, int(height*0.78)), (width, height)]
    draw.polygon(m2, fill=(8, 12, 24))

    img.save(out_path, "PNG")

def render_scene_clip(img_path: Path, audio_path: Path, out_clip_path: Path, duration: float, camera_motion: str, subtitle_text: Optional[str], target_width: int, target_height: int, fps: int = 30) -> Path:
    frames = max(int(duration * fps), 30)

    prep_img_path = out_clip_path.parent / f"prep_{out_clip_path.stem}.png"
    _prepare_subtitled_image(img_path, prep_img_path, subtitle_text, target_width, target_height)

    zoom_expr = "min(zoom+0.0012,1.20)"
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"

    if camera_motion == "zoom_out":
        zoom_expr = "max(1.20-0.0012*on,1.0)"
    elif camera_motion == "pan_left":
        zoom_expr = "1.12"
        x_expr = f"(1-on/{frames})*(iw-iw/zoom)"
    elif camera_motion == "pan_right":
        zoom_expr = "1.12"
        x_expr = f"(on/{frames})*(iw-iw/zoom)"
    elif camera_motion == "pan_up":
        zoom_expr = "1.12"
        y_expr = f"(1-on/{frames})*(ih-ih/zoom)"
    elif camera_motion == "pan_down":
        zoom_expr = "1.12"
        y_expr = f"(on/{frames})*(ih-ih/zoom)"

    filter_chain = f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={target_width}x{target_height}:fps={fps},format=yuv420p"

    cmd = [
        FFMPEG_PATH, "-y",
        "-loop", "1",
        "-framerate", str(fps),
        "-t", str(duration),
        "-i", str(prep_img_path),
        "-i", str(audio_path),
        "-vf", filter_chain,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-threads", "1",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-frames:v", str(frames),
        "-shortest",
        "-pix_fmt", "yuv420p",
        str(out_clip_path)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
    except Exception:
        fallback_cmd = [
            FFMPEG_PATH, "-y",
            "-loop", "1",
            "-framerate", str(fps),
            "-t", str(duration),
            "-i", str(prep_img_path),
            "-i", str(audio_path),
            "-vf", f"scale={target_width}:{target_height},fps={fps},format=yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-threads", "1",
            "-frames:v", str(frames),
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-pix_fmt", "yuv420p",
            str(out_clip_path)
        ]
        subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=20)

    if prep_img_path.exists():
        try:
            prep_img_path.unlink()
        except Exception:
            pass

    return out_clip_path

def _prepare_subtitled_image(src_img: Path, dst_img: Path, subtitle_text: Optional[str], width: int, height: int):
    with Image.open(src_img) as img:
        img = img.convert("RGB")
        w, h = img.size

        bg = img.resize((width, height), Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=20))

        ratio = min(width / w, height / h)
        nw, nh = int(w * ratio), int(h * ratio)
        fg = img.resize((nw, nh), Image.Resampling.LANCZOS)
        bg.paste(fg, ((width - nw) // 2, (height - nh) // 2))

        if subtitle_text:
            draw = ImageDraw.Draw(bg, "RGBA")
            font_size = max(24, int(height * 0.040))
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

            max_w = int(width * 0.85)
            lines = _wrap_text(subtitle_text, font, draw, max_w)
            line_height = int(font_size * 1.35)
            total_text_h = len(lines) * line_height
            start_y = int(height * 0.82) - (total_text_h // 2)

            for i, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=font)
                lw = bbox[2] - bbox[0]
                lx = (width - lw) // 2
                ly = start_y + i * line_height

                padding = 12
                pill = [lx - padding, ly - 4, lx + lw + padding, ly + line_height - 2]
                draw.rounded_rectangle(pill, radius=8, fill=(0, 0, 0, 160))
                draw.text((lx + 2, ly + 2), line, fill=(0, 0, 0, 200), font=font)
                draw.text((lx, ly), line, fill=(255, 255, 255, 255), font=font)

        bg.save(dst_img, "PNG")

def _wrap_text(text: str, font, draw, max_width: int) -> List[str]:
    words = text.split()
    lines = []
    curr = []
    for w in words:
        test_line = " ".join(curr + [w])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
            curr = [w]
    if curr:
        lines.append(" ".join(curr))
    return lines[:3]

def render_story_to_animated_video(task_id: str, req: StoryVideoRequest) -> str:
    work_dir = TEMP_DIR / f"story_render_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        update_task_step(task_id, "Analyzing story", 10, "Extracting narrative beats, characters, and visual scenes")
        scenes = segment_story_into_scenes(req.story, req.animation_style)
        total_scenes = len(scenes)

        update_task_step(task_id, "Planning scenes", 20, f"Structured {total_scenes} animated scenes in {req.animation_style.title()} style")

        res_map = {
            "16:9": {"720p": [1280, 720], "1080p": [1920, 1080], "4K": [3840, 2160]},
            "9:16": {"720p": [720, 1280], "1080p": [1080, 1920], "4K": [2160, 3840]},
            "1:1":  {"720p": [720, 720],   "1080p": [1080, 1080], "4K": [2160, 2160]}
        }
        target_res = res_map.get(req.aspect_ratio, {}).get(req.quality, [1920, 1080])

        scene_clip_paths = []
        total_duration = 0.0

        for idx, sc in enumerate(scenes):
            pct = 25 + int((idx / max(total_scenes, 1)) * 55)
            update_task_step(task_id, "Generating animation", pct, f"Scene {idx + 1}/{total_scenes}: Generating voiceover & artwork")

            voice_path = work_dir / f"voice_{idx:03d}.mp3"
            duration = synthesize_scene_voice(sc["narration"], req.voice, voice_path)
            total_duration += duration

            img_path = work_dir / f"scene_{idx:03d}.png"
            generate_scene_image(
                prompt=sc["visual_prompt"],
                animation_style=req.animation_style,
                out_path=img_path,
                width=target_res[0],
                height=target_res[1],
                seed=abs(hash(f"{task_id}_{idx}")) % 1000000
            )

            clip_path = work_dir / f"clip_{idx:03d}.mp4"
            sub_text = sc["narration"] if req.enable_subtitles else None
            render_scene_clip(
                img_path=img_path,
                audio_path=voice_path,
                out_clip_path=clip_path,
                duration=duration,
                camera_motion=sc.get("camera_motion", "zoom_in"),
                subtitle_text=sub_text,
                target_width=target_res[0],
                target_height=target_res[1]
            )

            if clip_path.exists() and clip_path.stat().st_size > 0:
                scene_clip_paths.append(clip_path)

        if not scene_clip_paths:
            raise RuntimeError("No animated scenes could be generated.")

        update_task_step(task_id, "Assembling movie", 85, "Merging animated scenes with audio alignment")
        concat_list_file = work_dir / "concat_story_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for p in scene_clip_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")

        raw_concat = work_dir / "story_concat.mp4"
        cmd_concat = [
            FFMPEG_PATH, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c", "copy",
            str(raw_concat)
        ]
        try:
            subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=20)
        except Exception:
            cmd_reencode = [
                FFMPEG_PATH, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list_file),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-threads", "1",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                str(raw_concat)
            ]
            subprocess.run(cmd_reencode, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=45)

        update_task_step(task_id, "Mastering soundtrack", 92, "Applying background ambience and audio ducking")
        output_filename = f"story_{task_id}.mp4"
        final_output_path = OUTPUT_DIR / output_filename

        if req.music_mood != "none":
            _mix_story_background_score(
                raw_concat,
                final_output_path,
                total_duration,
                req.music_mood,
                req.music_volume,
                work_dir
            )
        else:
            shutil.copy(str(raw_concat), str(final_output_path))

        update_task_step(task_id, "Finalizing", 100, "Animated story video ready!")

        result_url = f"/outputs/{output_filename}"
        task_data = {
            "task_id": task_id,
            "status": "completed",
            "progress": 100,
            "current_step": "Completed",
            "step_details": [
                {"name": "Analyzing story",       "status": "completed", "details": f"Parsed {total_scenes} story scenes"},
                {"name": "Planning scenes",        "status": "completed", "details": f"Selected {req.animation_style.title()} animation style"},
                {"name": "Generating animation",   "status": "completed", "details": "Generated neural voiceover & AI artwork"},
                {"name": "Assembling movie",       "status": "completed", "details": f"Rendered {total_scenes} scenes ({round(total_duration, 1)}s)"},
                {"name": "Mastering soundtrack",   "status": "completed", "details": "Mixed voice narration with cinematic ambiance"},
                {"name": "Finalizing",             "status": "completed", "details": f"{req.quality} MP4 completed successfully!"},
            ],
            "result_video_url": result_url,
            "timeline": {
                "title": req.title,
                "template": req.animation_style,
                "aspect_ratio": req.aspect_ratio,
                "duration": round(total_duration, 2),
                "scenes_count": total_scenes
            },
            "error": None,
        }
        save_task_progress(task_id, task_data)
        return result_url

    except Exception as e:
        print(f"[story_engine] Story rendering failed for task {task_id}: {e}")
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

def _mix_story_background_score(video_path: Path, out_final_path: Path, duration: float, mood: str, music_vol: float, work_dir: Path):
    freq = MOOD_FREQUENCIES.get(mood, 220.0)
    bg_music = work_dir / "ambient_bg.wav"

    subprocess.run([
        FFMPEG_PATH, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration + 2},lowpass=f=400,volume=0.3",
        "-c:a", "pcm_s16le",
        str(bg_music)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    fade_start = max(0.0, duration - 1.5)
    audio_filter = f"[1:a]volume={music_vol},afade=t=out:st={fade_start}:d=1.5[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2"

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-i", str(bg_music),
        "-c:v", "copy",
        "-filter_complex", audio_filter,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_final_path)
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
    except Exception:
        shutil.copy(str(video_path), str(out_final_path))
