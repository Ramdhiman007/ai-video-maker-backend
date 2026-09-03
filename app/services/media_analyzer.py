import os
import subprocess
import json
import cv2
import numpy as np
from pathlib import Path
from PIL import Image
from typing import Dict, Any, List, Tuple
from app.config import FFMPEG_PATH, FFPROBE_PATH, TEMP_DIR, UPLOAD_DIR

# Load OpenCV face detector Haar cascade safely
face_cascade = None
try:
    if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
        cascade_file = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        if os.path.exists(cascade_file) and hasattr(cv2, 'CascadeClassifier'):
            face_cascade = cv2.CascadeClassifier(cascade_file)
except Exception as e:
    print(f"OpenCV face cascade initialization warning: {e}")

def analyze_photo(file_path: Path) -> Dict[str, Any]:
    """Analyzes a photo file: resolution, orientation, faces, quality score."""
    with Image.open(file_path) as img:
        width, height = img.size
        
    orientation = "landscape" if width > height else ("portrait" if height > width else "square")
    
    # Face detection using OpenCV
    faces_count = 0
    face_boxes = []
    quality_score = 0.8
    
    try:
        img_cv = cv2.imread(str(file_path))
        if img_cv is not None:
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            if face_cascade is not None and not face_cascade.empty():
                detected_faces = face_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
                )
                faces_count = len(detected_faces)
                face_boxes = [[int(x), int(y), int(w), int(h)] for (x, y, w, h) in detected_faces]
            
            # Calculate image brightness and sharpness (quality estimation)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            brightness = np.mean(gray)
            quality_score = round(min(1.0, (laplacian_var / 500.0) * 0.5 + (brightness / 255.0) * 0.5), 2)
    except Exception as e:
        print(f"Error analyzing image opencv stats: {e}")

    # Create thumbnail
    thumb_path = generate_photo_thumbnail(file_path)

    return {
        "width": width,
        "height": height,
        "orientation": orientation,
        "faces_count": faces_count,
        "face_boxes": face_boxes,
        "quality_score": quality_score,
        "thumbnail": thumb_path
    }

def generate_photo_thumbnail(file_path: Path) -> str:
    """Generates a 400px thumbnail for an image."""
    thumb_filename = f"thumb_{file_path.stem}.jpg"
    thumb_path = UPLOAD_DIR / thumb_filename
    if not thumb_path.exists():
        try:
            with Image.open(file_path) as img:
                img.convert("RGB").thumbnail((400, 400))
                img.save(thumb_path, "JPEG", quality=85)
        except Exception as e:
            print(f"Error creating photo thumbnail: {e}")
    return thumb_filename

def analyze_video(file_path: Path) -> Dict[str, Any]:
    """Analyzes a video file using ffprobe and extracts duration, resolution, orientation."""
    cmd = [
        FFPROBE_PATH,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path)
    ]
    
    width = 1280
    height = 720
    duration = 5.0
    orientation = "landscape"
    
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        info = json.loads(result.stdout)
        
        video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
        format_info = info.get("format", {})
        
        if video_stream:
            width = int(video_stream.get("width", 1280))
            height = int(video_stream.get("height", 720))
            
            # Check for rotation tag
            tags = video_stream.get("tags", {})
            rotate = tags.get("rotate", "0")
            if rotate in ["90", "270", "-90"]:
                width, height = height, width
                
        if "duration" in format_info:
            duration = float(format_info["duration"])
        elif video_stream and "duration" in video_stream:
            duration = float(video_stream["duration"])
            
        orientation = "landscape" if width > height else ("portrait" if height > width else "square")
    except Exception as e:
        print(f"Error running ffprobe on video {file_path}: {e}")
        
    # Generate video thumbnail keyframe
    thumb_filename = generate_video_thumbnail(file_path, duration)

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "orientation": orientation,
        "faces_count": 0,
        "face_boxes": [],
        "quality_score": 0.85,
        "thumbnail": thumb_filename
    }

def generate_video_thumbnail(file_path: Path, duration: float) -> str:
    """Extracts a keyframe thumbnail from a video file."""
    thumb_filename = f"thumb_{file_path.stem}.jpg"
    thumb_path = UPLOAD_DIR / thumb_filename
    if not thumb_path.exists():
        timestamp = max(0.5, min(1.0, duration / 2))
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-ss", str(timestamp),
            "-i", str(file_path),
            "-vframes", "1",
            "-vf", "scale=400:-1",
            "-q:v", "3",
            str(thumb_path)
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except Exception as e:
            print(f"Error generating video thumbnail: {e}")
    return thumb_filename
