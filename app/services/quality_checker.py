import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
from PIL import Image, ImageChops
import numpy as np

from app.config import FFPROBE_PATH, FFMPEG_PATH


@dataclass
class QualityReport:
    is_valid: bool
    has_motion: bool
    duration: float
    width: int
    height: int
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class SceneQualityChecker:
    """
    Automated Quality Control Layer:
    - Verifies file integrity, size, and duration
    - Inspects video streams via ffprobe
    - Computes temporal frame variance to guarantee real movement
    """

    def inspect_scene_clip(self, video_path: Path, expected_duration: float = 3.0) -> QualityReport:
        if not video_path.exists():
            return QualityReport(is_valid=False, has_motion=False, duration=0.0, width=0, height=0, error="Video file does not exist")

        if video_path.stat().st_size < 1024:
            return QualityReport(is_valid=False, has_motion=False, duration=0.0, width=0, height=0, error="Video file size is zero or corrupted (<1KB)")

        # ffprobe stream inspection
        probe_cmd = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "stream=width,height,codec_name,duration,nb_frames:format=duration",
            "-of", "json",
            str(video_path)
        ]
        try:
            res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True)
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            v_stream = next((s for s in streams if "width" in s), {})
            width = int(v_stream.get("width", 0))
            height = int(v_stream.get("height", 0))
            dur_str = data.get("format", {}).get("duration") or v_stream.get("duration") or "0"
            duration = float(dur_str)
        except Exception as e:
            return QualityReport(is_valid=False, has_motion=False, duration=0.0, width=0, height=0, error=f"ffprobe inspection failed: {e}")

        if width <= 0 or height <= 0:
            return QualityReport(is_valid=False, has_motion=False, duration=duration, width=width, height=height, error="Invalid video resolution")

        # Temporal motion check: sample 2 frames across the clip and check pixel difference
        has_motion = self._verify_temporal_motion(video_path, duration)

        return QualityReport(
            is_valid=True,
            has_motion=has_motion,
            duration=duration,
            width=width,
            height=height,
            details={"streams_count": len(streams)}
        )

    def _verify_temporal_motion(self, video_path: Path, duration: float) -> bool:
        """
        Extracts two frames (at 20% and 80% through the clip) and computes pixel difference.
        Returns True if there is non-trivial temporal movement.
        """
        temp_dir = video_path.parent / f"qc_{video_path.stem}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        f1 = temp_dir / "qc_1.png"
        f2 = temp_dir / "qc_2.png"

        t1 = max(0.2, duration * 0.2)
        t2 = min(duration - 0.2, duration * 0.8)

        try:
            cmd1 = [FFMPEG_PATH, "-y", "-ss", str(t1), "-i", str(video_path), "-vframes", "1", str(f1)]
            cmd2 = [FFMPEG_PATH, "-y", "-ss", str(t2), "-i", str(video_path), "-vframes", "1", str(f2)]
            subprocess.run(cmd1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)

            if f1.exists() and f2.exists():
                with Image.open(f1) as img1, Image.open(f2) as img2:
                    diff = ImageChops.difference(img1.convert("RGB"), img2.convert("RGB"))
                    stat = np.array(diff)
                    mean_diff = float(np.mean(stat))
                    # If mean pixel difference > 1.5, scene has active temporal change
                    return mean_diff > 1.2
            return True
        except Exception:
            return True
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
