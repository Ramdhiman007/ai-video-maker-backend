import os
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.services.video_providers.base import (
    VideoGenerationProvider,
    VideoJobResult,
    ProviderNotConfiguredError,
    VideoGenerationError,
)


class KlingVideoProvider(VideoGenerationProvider):
    """
    Adapter for Kling AI Video Generation API.
    Supports high-fidelity physics-based motion.
    """

    BASE_URL = "https://api.klingai.com/v1"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = (
            api_key
            or os.getenv("KLING_API_KEY")
            or os.getenv("VIDEO_PROVIDER_API_KEY")
            or ""
        )

    @property
    def name(self) -> str:
        return "Kling AI Video"

    def is_configured(self) -> bool:
        return bool(self._api_key and len(self._api_key.strip()) > 10)

    def get_required_env_vars(self) -> List[str]:
        return ["KLING_API_KEY", "VIDEO_PROVIDER_API_KEY"]

    def _ensure_configured(self):
        if not self.is_configured():
            raise ProviderNotConfiguredError("Kling AI", self.get_required_env_vars())

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def generate_text_to_video(
        self,
        prompt: str,
        motion_prompt: str,
        camera_motion: str,
        duration: float,
        aspect_ratio: str,
        out_path: Path,
        **kwargs
    ) -> VideoJobResult:
        self._ensure_configured()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        full_prompt = f"{prompt}. Motion details: {motion_prompt}. Camera: {camera_motion}."
        payload = {
            "model_name": "kling-v1",
            "prompt": full_prompt,
            "aspect_ratio": "16:9" if aspect_ratio != "9:16" else "9:16",
            "duration": "5" if duration <= 5 else "10",
        }

        url = f"{self.BASE_URL}/videos/text2video"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                task_id = data.get("data", {}).get("task_id")
                if not task_id:
                    raise VideoGenerationError(self.name, f"Kling returned no task ID: {data}")
        except Exception as e:
            raise VideoGenerationError(self.name, str(e))

        return self._poll_task(task_id, out_path, duration, {"prompt": full_prompt}, **kwargs)

    def generate_image_to_video(
        self,
        image_path: Path,
        prompt: str,
        motion_prompt: str,
        duration: float,
        aspect_ratio: str,
        out_path: Path,
        **kwargs
    ) -> VideoJobResult:
        self._ensure_configured()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        import base64
        img_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")

        payload = {
            "model_name": "kling-v1",
            "image": img_b64,
            "prompt": f"{prompt}. Motion: {motion_prompt}.",
            "duration": "5" if duration <= 5 else "10",
        }

        url = f"{self.BASE_URL}/videos/image2video"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                task_id = data.get("data", {}).get("task_id")
        except Exception as e:
            raise VideoGenerationError(self.name, str(e))

        return self._poll_task(task_id, out_path, duration, {"source_image": str(image_path)}, **kwargs)

    def _poll_task(self, task_id: str, out_path: Path, duration: float, metadata: Dict[str, Any], **kwargs) -> VideoJobResult:
        poll_url = f"{self.BASE_URL}/videos/tasks/{task_id}"
        t0 = time.time()
        timeout = int(kwargs.get("timeout_seconds", 240))

        while time.time() - t0 < timeout:
            time.sleep(5)
            req = urllib.request.Request(poll_url, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                continue

            status = data.get("data", {}).get("task_status")
            if status == "succeed":
                works = data.get("data", {}).get("task_result", {}).get("videos", [])
                if not works:
                    raise VideoGenerationError(self.name, "Kling task succeeded but no video url returned.")
                video_url = works[0].get("url")
                urllib.request.urlretrieve(video_url, str(out_path))
                return VideoJobResult(
                    job_id=task_id,
                    status="completed",
                    provider=self.name,
                    video_url=video_url,
                    local_path=out_path,
                    duration=duration,
                    metadata=metadata,
                )
            elif status in ["failed", "canceled"]:
                msg = data.get("data", {}).get("task_status_msg", "Task failed")
                raise VideoGenerationError(self.name, f"Kling task {task_id} {status}: {msg}")

        raise VideoGenerationError(self.name, f"Generation timed out after {timeout}s waiting for Kling AI.")
