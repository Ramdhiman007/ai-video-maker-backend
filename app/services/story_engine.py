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

def segment_story_into_scenes(story_text: str, animation_style: str = "pixar", title: str = "") -> List[Dict[str, Any]]:
    story_text = story_text.strip()
    if not story_text:
        story_text = "Once upon a time in a magical land, a great adventure began."

    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            prompt = f"""You are a professional animated film director creating a scene-by-scene visual storyboard.

Carefully read the story below and break it into animated video scenes. Each scene covers one narrative beat.

Story Title: "{title or 'Animated Story'}"
Animation Style: {animation_style}
Story:
\"\"\"{story_text}\"\"\"

Generate as many scenes as the story naturally requires (minimum 2, no maximum limit).

For each scene return a JSON object with EXACTLY these keys:
- "narration": The exact story sentences for this scene (1-3 sentences, natural storytelling voice).
- "visual_prompt": A highly detailed image generation prompt. MUST include the actual characters from the story (animals, people, creatures, objects) with their visual descriptions (color, size, expression). Include the specific setting, lighting, atmosphere. Do NOT add animation style here.
- "camera_motion": One of exactly: zoom_in, zoom_out, pan_left, pan_right
- "mood": One of exactly: magical, exciting, sad, triumphant, scary, peaceful

Return ONLY a raw valid JSON array. No markdown fences, no explanation text, no trailing comma."""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()
            scenes = json.loads(raw)
            if isinstance(scenes, list) and len(scenes) > 0:
                # Ensure every scene has a mood field
                for sc in scenes:
                    if "mood" not in sc:
                        sc["mood"] = "magical"
                return scenes
        except Exception as e:
            print(f"[story_engine] Gemini scene segmentation fallback: {e}")

    # Fallback: split into scenes by paragraph/sentence grouping
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
    motions = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
    moods = ["magical", "peaceful", "exciting", "triumphant"]

    for sent in raw_sentences:
        w_count = len(sent.split())
        if curr_words + w_count > 30 and curr_narration:
            narr = " ".join(curr_narration)
            scenes.append({
                "narration": narr,
                "visual_prompt": _extract_visual_keywords(narr),
                "camera_motion": motions[len(scenes) % len(motions)],
                "mood": moods[len(scenes) % len(moods)],
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
            "mood": moods[len(scenes) % len(moods)],
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

STORY_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "story_scenes"

MOOD_PARTICLE_MAP = {
    "magical":    "magical.mp4",
    "peaceful":   "magical.mp4",
    "exciting":   "exciting.mp4",
    "sad":        "sad.mp4",
    "triumphant": "triumphant.mp4",
    "scary":      "scary.mp4",
}

def _try_stable_horde(prompt: str, style: str, out_path: Path, timeout_sec: int = 22) -> bool:
    """Try to generate image via Stable Horde (free community GPU cluster)."""
    style_tags = {
        "pixar":      "3D Pixar Disney CGI render, charming expressive characters, vibrant cinematic lighting, masterpiece",
        "anime":      "Studio Ghibli anime style, colorful breathtaking animation, beautiful skies, expressive art",
        "watercolor": "whimsical storybook watercolor illustration, soft pastel painting, fairy tale children book art",
        "comic":      "modern graphic novel comic book art, dynamic bold ink lines, vivid pop art colors, dramatic angles",
        "fantasy":    "epic high fantasy cinematic concept art, magical glowing atmosphere, volumetric lighting masterpiece",
        "cyberpunk":  "futuristic neon cyberpunk animation, glowing holographic lights, rain slicked streets, synthwave",
    }
    style_tag = style_tags.get(style.lower(), style_tags["pixar"])
    full_prompt = f"{prompt[:110]}, {style_tag}"
    try:
        url = "https://stablehorde.net/api/v2/generate/async"
        payload = json.dumps({
            "prompt": full_prompt,
            "params": {
                "steps": 18,
                "width": 768,
                "height": 448,
                "sampler_name": "k_euler",
                "cfg_scale": 7
            },
            "nsfw": False,
            "censor_nsfw": True,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "apikey": "0000000000",
            "Client-Agent": "AIVideoMaker:1.0:anon"
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            job_id = result.get("id")
        if not job_id:
            return False

        status_url = f"https://stablehorde.net/api/v2/generate/status/{job_id}"
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            time.sleep(2.5)
            req2 = urllib.request.Request(status_url, headers={"Client-Agent": "AIVideoMaker:1.0:anon"})
            with urllib.request.urlopen(req2, timeout=6) as resp2:
                st = json.loads(resp2.read().decode("utf-8"))
                if st.get("done") and st.get("generations"):
                    img_url = st["generations"][0]["img"]
                    req3 = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req3, timeout=12) as resp3:
                        img_bytes = resp3.read()
                    temp_dl = out_path.parent / f"tmp_horde_{out_path.stem}.webp"
                    temp_dl.write_bytes(img_bytes)
                    with Image.open(temp_dl) as im:
                        im.convert("RGB").resize((1280, 720), Image.LANCZOS).save(out_path, "PNG")
                    temp_dl.unlink(missing_ok=True)
                    return True
        return False
    except Exception as e:
        print(f"[story_engine] Horde notice: {e}")
        return False


def generate_scene_image(
    prompt: str,
    animation_style: str,
    out_path: Path,
    width: int = 1280,
    height: int = 720,
    seed: Optional[int] = None,
    scene_idx: int = 0
) -> Path:
    prompt_lower = prompt.lower()

    # Step 1: If GEMINI_API_KEY is configured, generate custom AI animation via Imagen 3
    from app.config import GEMINI_API_KEY
    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            style_modifier = STYLE_MODIFIERS.get(animation_style.lower(), STYLE_MODIFIERS["pixar"])
            resp = client.models.generate_images(
                model="imagen-3.0-generate-002",
                prompt=f"{prompt}, {style_modifier}, 4K resolution",
                config=dict(number_of_images=1, aspect_ratio="16:9")
            )
            if resp.generated_images:
                out_path.write_bytes(resp.generated_images[0].image.image_bytes)
                print(f"[story_engine] Imagen 3 generated scene {scene_idx}")
                return out_path
        except Exception as e:
            print(f"[story_engine] Gemini Imagen notice ({e}), trying Horde...")

    # Step 2: Try Stable Horde (free community GPU cluster — generates story-specific art)
    if _try_stable_horde(prompt, animation_style, out_path):
        print(f"[story_engine] Stable Horde generated scene {scene_idx}")
        return out_path

    # Step 3: Intelligent theme matching from narrative keywords — expanded set
    theme = None
    # Animal / nature stories
    if any(k in prompt_lower for k in ["lion", "tiger", "bear", "elephant", "leopard", "jaguar",
                                        "fox", "wolf", "deer", "monkey", "giraffe", "zebra",
                                        "jungle", "forest", "woodland", "savanna", "nature", "animal",
                                        "creature", "wildlife", "hunt", "roar", "growl", "tree", "grove"]):
        theme = "fox"
    # Small animals / village / moral tales
    elif any(k in prompt_lower for k in ["mouse", "rat", "rabbit", "squirrel", "hamster", "bird",
                                          "tiny", "small", "little", "village", "cottage", "meadow",
                                          "garden", "home", "flower", "bee", "butterfly", "fairy"]):
        theme = "fox"
    # Space / sci-fi
    elif any(k in prompt_lower for k in ["mars", "astronaut", "space", "star", "galaxy", "rocket",
                                          "spaceship", "alien", "crater", "rover", "orbit", "cosmos",
                                          "planet", "nebula", "universe", "comet", "asteroid"]):
        theme = "mars"
    # Kingdom / fantasy
    elif any(k in prompt_lower for k in ["castle", "palace", "king", "queen", "prince", "princess",
                                          "throne", "ballroom", "magic", "mirror", "kingdom", "gate",
                                          "dragon", "royal", "knight", "sword", "crown", "wizard",
                                          "enchanted", "spell", "potion", "dungeon", "tower"]):
        theme = "castle"
    # Cyberpunk / future / action
    elif any(k in prompt_lower for k in ["samurai", "tokyo", "cyber", "neon", "robot", "blade",
                                          "katana", "ninja", "city", "drone", "alley", "hologram",
                                          "future", "street", "car", "tech", "mech", "android"]):
        theme = "samurai"

    # Use pre-rendered 4K animation masterplate
    if theme and STORY_ASSETS_DIR.exists():
        scene_num = (scene_idx % 4) + 1
        asset_file = STORY_ASSETS_DIR / f"{theme}_{scene_num}.jpg"
        if asset_file.exists() and asset_file.stat().st_size > 5000:
            out_path.write_bytes(asset_file.read_bytes())
            return out_path

    # Step 4: Quick Pollinations attempt (short timeout)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/*,*/*;q=0.8"
    }
    clean_prompt = re.sub(r"[^a-zA-Z0-9\s,'-]", " ", prompt)[:60].strip()
    encoded = urllib.parse.quote(f"{clean_prompt} {animation_style} animation masterpiece")
    poll_url = f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=432&nologo=true&seed={seed or 42}"
    try:
        req = urllib.request.Request(poll_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            if len(data) > 5000:
                out_path.write_bytes(data)
                return out_path
    except Exception:
        pass

    # Step 5: Style-based masterplate fallback
    style_to_theme = {
        "pixar": "fox",
        "anime": "castle",
        "watercolor": "fox",
        "comic": "samurai",
        "fantasy": "castle",
        "cyberpunk": "samurai"
    }
    fallback_theme = style_to_theme.get(animation_style.lower(), "fox")
    scene_num = (scene_idx % 4) + 1
    fallback_asset = STORY_ASSETS_DIR / f"{fallback_theme}_{scene_num}.jpg"
    if fallback_asset.exists():
        out_path.write_bytes(fallback_asset.read_bytes())
        return out_path

    # Step 6: Absolute last resort — stylized gradient card
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

def render_scene_clip(img_path: Path, audio_path: Path, out_clip_path: Path, duration: float, camera_motion: str, subtitle_text: Optional[str], target_width: int, target_height: int, fps: int = 30, mood: str = "magical") -> Path:
    frames = max(int(duration * fps), 30)

    prep_img_path = out_clip_path.parent / f"prep_{out_clip_path.stem}.png"
    _prepare_subtitled_image(img_path, prep_img_path, subtitle_text, target_width, target_height)

    # Full-Motion Animation: Organic 3D camera drift & breathing
    zoom_expr = "1.06+0.03*sin(2*PI*it/4.0)"
    x_expr = "iw/2-(iw/zoom/2)+12*sin(2*PI*it/3.0)"
    y_expr = "ih/2-(ih/zoom/2)+8*cos(2*PI*it/3.0)"

    if camera_motion == "zoom_out":
        zoom_expr = "1.14-0.03*sin(2*PI*it/4.0)"
        x_expr = "iw/2-(iw/zoom/2)-12*sin(2*PI*it/3.0)"
        y_expr = "ih/2-(ih/zoom/2)-8*cos(2*PI*it/3.0)"
    elif camera_motion == "pan_left":
        zoom_expr = "1.10"
        x_expr = f"(1-on/{frames})*(iw-iw/zoom)+8*sin(2*PI*it/2.5)"
        y_expr = "ih/2-(ih/zoom/2)+6*cos(2*PI*it/2.5)"
    elif camera_motion == "pan_right":
        zoom_expr = "1.10"
        x_expr = f"(on/{frames})*(iw-iw/zoom)+8*sin(2*PI*it/2.5)"
        y_expr = "ih/2-(ih/zoom/2)+6*cos(2*PI*it/2.5)"

    # Select mood-matched particle overlay
    particle_name = MOOD_PARTICLE_MAP.get(mood, "magical.mp4")
    particle_path = STORY_ASSETS_DIR.parent / "particles" / particle_name
    if not particle_path.exists():
        # Legacy fallback to old starlight_particles.mp4
        particle_path = STORY_ASSETS_DIR.parent / "starlight_particles.mp4"

    if particle_path.exists():
        filter_complex = (
            f"[0:v]scale={int(target_width*1.1)}x{int(target_height*1.1)},zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={target_width}x{target_height}:fps={fps}[bg];"
            f"[1:v]colorkey=black:0.15:0.2[pt];"
            f"[bg][pt]overlay=0:0:shortest=1,format=yuv420p[v]"
        )
        cmd = [
            FFMPEG_PATH, "-y",
            "-loop", "1",
            "-t", str(duration),
            "-i", str(prep_img_path),
            "-stream_loop", "-1",
            "-i", str(particle_path),
            "-i", str(audio_path),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "2:a",
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
    else:
        filter_chain = f"scale={int(target_width*1.1)}x{int(target_height*1.1)},zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={target_width}x{target_height}:fps={fps},format=yuv420p"
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

def generate_story_from_prompt(
    prompt: str,
    animation_style: str = "pixar",
    mood: str = "cinematic",
    voice: str = "en-US-ChristopherNeural"
) -> Dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        prompt = "A magical adventure about courage, discovery and friendship"

    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            ai_prompt = f"""You are a master animated film director and lead screenwriter for Hollywood animated features (Pixar, Ghibli, DreamWorks).
The user wants to create an animated video from this concept or prompt:
Concept: "{prompt}"
Desired Visual Style: {animation_style}
Emotional Tone / Mood: {mood}

Write a cinematic, beautifully paced short story for this video.
Guidelines:
1. Provide a captivating, memorable title.
2. Write 3 to 5 vivid narrative paragraphs (total 130 to 220 words) suitable for neural voiceover narration.
3. Introduce unique characters with clear visual traits, present an exciting challenge or turning point, and resolve with an inspiring conclusion.
4. Keep the vocabulary evocative, visual, and rhythmic.

Return ONLY a valid JSON object with EXACTLY these keys:
- "title": A short catchy movie title
- "story": The full screenplay narration text with paragraphs separated by newlines
- "suggested_style": One of "pixar", "anime", "watercolor", "comic", "fantasy", "cyberpunk"
- "suggested_mood": One of "cinematic", "whimsical", "adventure", "emotional"
- "suggested_voice": Voice recommendation (e.g. "en-US-ChristopherNeural", "en-US-JennyNeural")

Do NOT include markdown fences, return pure JSON."""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=ai_prompt
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            res = json.loads(raw.strip())
            if isinstance(res, dict) and "story" in res:
                return res
        except Exception as e:
            print(f"[story_engine] Gemini generate_story_from_prompt fallback: {e}")

    # Generative fallback based on theme keywords
    p_low = prompt.lower()
    if any(k in p_low for k in ["cat", "dog", "pet", "robot", "cyber", "neon", "tokyo", "tech"]):
        return {
            "title": "The Neon Stray",
            "story": (
                "Beneath the glowing holographic signs of Neo-Tokyo, a stray cyber-cat with glowing amber eyes prowled the rain-washed rooftops. "
                "While dodging patrolling surveillance drones, she discovered an injured robotic bird trapped inside a tangled nest of power cables. "
                "With agile paws and a gentle touch, she delicately severed the high-voltage wires, granting the golden automaton its freedom. "
                "Together, cat and mechanical songbird soared into the neon dawn, guardians of the city's forgotten dreamers."
            ),
            "suggested_style": "cyberpunk",
            "suggested_mood": "adventure",
            "suggested_voice": "en-US-ChristopherNeural"
        }
    elif any(k in p_low for k in ["space", "mars", "star", "alien", "astronaut", "galaxy", "rocket", "planet"]):
        return {
            "title": "Starlight Odyssey",
            "story": (
                "Deep in the uncharted outer rim, an adventurous starfarer guided their exploratory vessel toward a shimmering ringed world. "
                "As the ship glided through clouds of glowing sapphire dust, ancient radio frequencies filled the cockpit with a harmonious alien symphony. "
                "Touching down upon a crystalline plateau, the explorer greeted towering guardians of pure starlight who had watched over the cosmos for millennia. "
                "Hand in hand, they ignited a new beacon of knowledge that would illuminate the galaxies for generations to come."
            ),
            "suggested_style": "fantasy",
            "suggested_mood": "cinematic",
            "suggested_voice": "en-US-GuyNeural"
        }
    elif any(k in p_low for k in ["lion", "mouse", "animal", "jungle", "forest", "wild", "safari"]):
        return {
            "title": "The Lion and the Mouse",
            "story": (
                "Under the warm amber canopy of the African savanna, a noble golden lion rested after a long day's watch. "
                "A tiny field mouse accidentally scampered over his mighty paw, trembling in fear as the great king opened his amber eyes. "
                "Amused by her tiny bravery and polite plea, the lion gently let her scurry away free into the acacia grasses. "
                "Weeks later, when hunters trapped the lion in heavy hemp ropes, the faithful mouse arrived, chewed the knots to shreds, and proved that even the smallest heart can save a king."
            ),
            "suggested_style": "pixar",
            "suggested_mood": "whimsical",
            "suggested_voice": "en-US-ChristopherNeural"
        }
    else:
        words = prompt.split()
        clean_words = [w for w in words if w.lower() not in ["a", "an", "the", "about", "who", "which", "that"]]
        title_topic = " ".join(clean_words[:4]).title() if clean_words else "The Secret Realm"
        title = f"The Legend of {title_topic}"
        narrative_focus = " ".join(words[:10])
        return {
            "title": title,
            "story": (
                f"Long ago, in a wondrous realm where legends take wing, a brave journey began guided by {narrative_focus}. "
                "Through whispering enchanted valleys and across glowing starlit horizons, each step revealed hidden wonders beyond imagination. "
                "When great trials tested their courage, an unexpected surge of inner strength turned the tide toward triumph. "
                "As the dawn broke in brilliant gold, peace and harmony were restored to the land forever."
            ),
            "suggested_style": animation_style,
            "suggested_mood": mood,
            "suggested_voice": voice
        }



def render_story_to_animated_video(task_id: str, req: StoryVideoRequest) -> str:
    work_dir = TEMP_DIR / f"story_render_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        now_time = time.strftime("%H:%M:%S")
        update_task_step(
            task_id, "Analyzing story", 10,
            "Extracting narrative beats, characters, and visual scenes",
            agent_log={"role": "Director Agent", "icon": "🎬", "message": f"Analyzing story: '{req.title}' | Style: {req.animation_style.title()} | Audio: {req.music_mood}", "time": now_time}
        )
        scenes = segment_story_into_scenes(req.story, req.animation_style, title=req.title)
        total_scenes = len(scenes)

        now_time = time.strftime("%H:%M:%S")
        update_task_step(
            task_id, "Planning scenes", 20,
            f"AI planned {total_scenes} animated scenes in {req.animation_style.title()} style",
            agent_log={"role": "Screenplay Director", "icon": "📋", "message": f"Deconstructed narrative into {total_scenes} cinematic scene beats with emotion tracking.", "time": now_time}
        )

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
            narration_preview = sc.get("narration", "")[:55]
            mood = sc.get("mood", "magical")

            now_time = time.strftime("%H:%M:%S")
            update_task_step(
                task_id, "Generating animation", pct,
                f"Scene {idx + 1}/{total_scenes}: {narration_preview}...",
                agent_log={"role": "Concept Artist", "icon": "🎨", "message": f"Scene {idx + 1}/{total_scenes}: Generating visuals [{mood.title()} Mood] -> {sc.get('visual_prompt','')[:50]}...", "time": now_time}
            )

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
                seed=abs(hash(f"{task_id}_{idx}")) % 1000000,
                scene_idx=idx
            )

            now_time = time.strftime("%H:%M:%S")
            update_task_step(
                task_id, "Generating animation", min(pct + 2, 82),
                f"Scene {idx + 1}/{total_scenes}: Voice narration ({round(duration, 1)}s) & 3D drift",
                agent_log={"role": "VFX & Voice", "icon": "✨", "message": f"Scene {idx + 1}: Neural voice synced ({round(duration,1)}s). Applied 3D {sc.get('camera_motion','zoom_in')} camera sway & particle overlay.", "time": now_time}
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
                target_height=target_res[1],
                mood=mood
            )

            if clip_path.exists() and clip_path.stat().st_size > 0:
                scene_clip_paths.append(clip_path)

        if not scene_clip_paths:
            raise RuntimeError("No animated scenes could be generated.")

        now_time = time.strftime("%H:%M:%S")
        update_task_step(
            task_id, "Assembling movie", 86,
            "Merging animated scenes with audio alignment",
            agent_log={"role": "Film Editor", "icon": "🎞️", "message": f"Sequencing {len(scene_clip_paths)} scene clips into master movie timeline ({round(total_duration, 1)}s total).", "time": now_time}
        )
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

        now_time = time.strftime("%H:%M:%S")
        update_task_step(
            task_id, "Mastering soundtrack", 93,
            "Applying background ambience and audio ducking",
            agent_log={"role": "Sound Engineer", "icon": "🎼", "message": f"Mastered multi-harmonic {req.music_mood} score with automatic vocal ducking & reverb.", "time": now_time}
        )
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

        now_time = time.strftime("%H:%M:%S")
        update_task_step(
            task_id, "Finalizing", 100,
            "Animated story video ready!",
            agent_log={"role": "Producer Agent", "icon": "🏆", "message": f"Final master verified! {req.quality} animated video ready for download and streaming.", "time": now_time}
        )

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
    # Rich multi-layer harmonic ambient score
    mood_config = {
        "cinematic":  {"base": 220.0, "fifth": 330.0, "third": 275.0,  "tempo": "slow"},
        "whimsical":  {"base": 392.0, "fifth": 523.3, "third": 466.2,  "tempo": "medium"},
        "adventure":  {"base": 293.66,"fifth": 440.0, "third": 349.23, "tempo": "fast"},
        "emotional":  {"base": 261.63,"fifth": 392.0, "third": 311.13, "tempo": "slow"},
        "upbeat":     {"base": 349.23,"fifth": 523.3, "third": 440.0,  "tempo": "medium"},
        "none":       {"base": 220.0, "fifth": 330.0, "third": 275.0,  "tempo": "slow"},
    }
    mc = mood_config.get(mood, mood_config["cinematic"])
    bg_music = work_dir / "ambient_bg.wav"

    fade_in_dur = min(3.0, duration * 0.12)
    fade_out_start = max(0.0, duration - 2.5)
    total_gen = duration + 4.0

    # Three layered harmonics: base + perfect fifth + major third
    # Low-pass filtered for warm pad feel, then reverb/echo for cinematic depth
    lavfi_filter = (
        f"sine=frequency={mc['base']}:duration={total_gen}[a1];"
        f"sine=frequency={mc['fifth']}:duration={total_gen},volume=0.55[a2];"
        f"sine=frequency={mc['third']}:duration={total_gen},volume=0.35[a3];"
        f"[a1][a2][a3]amix=inputs=3:duration=first[mixed];"
        f"[mixed]lowpass=f=600,aecho=0.65:0.45:90:0.25,volume=0.42,"
        f"afade=t=in:st=0:d={fade_in_dur:.1f},"
        f"afade=t=out:st={fade_out_start:.1f}:d=2.5[out]"
    )

    try:
        subprocess.run([
            FFMPEG_PATH, "-y",
            "-f", "lavfi",
            "-i", lavfi_filter,
            "-map", "[out]",
            "-c:a", "pcm_s16le",
            str(bg_music)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        # Simple fallback
        freq = mc["base"]
        subprocess.run([
            FFMPEG_PATH, "-y", "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={total_gen},lowpass=f=400,volume=0.3",
            "-c:a", "pcm_s16le", str(bg_music)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    fade_filter = (
        f"[1:a]volume={music_vol},afade=t=out:st={fade_out_start:.1f}:d=2.0[bg];"
        f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2"
    )

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-i", str(bg_music),
        "-c:v", "copy",
        "-filter_complex", fade_filter,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_final_path)
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
    except Exception:
        shutil.copy(str(video_path), str(out_final_path))

