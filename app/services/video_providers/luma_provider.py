import os
import json
import time
import base64
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


class LumaDreamMachineProvider(VideoGenerationProvider):
    """
    Adapter for Luma Dream Machine API (https://api.lumalabs.ai).
    Provides genuine high-motion video generation from text and starting frame images.
    """

    API_URL = "https://api.lumalabs.ai/dream-machine/v1/generations"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = (
            api_key
            or os.getenv("LUMA_API_KEY")
            or os.getenv("VIDEO_PROVIDER_API_KEY")
            or ""
        )

    @property
    def name(self) -> str:
        return "Luma Dream Machine"

    def is_configured(self) -> bool:
        return bool(self._api_key and len(self._api_key.strip()) > 10)

    def get_required_env_vars(self) -> List[str]:
        return ["LUMA_API_KEY", "VIDEO_PROVIDER_API_KEY"]

    def _ensure_configured(self):
        if not self.is_configured():
            raise ProviderNotConfiguredError("Luma Dream Machine", self.get_required_env_vars())

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AIVideoMaker/2.0",
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

        full_prompt = (
            f"{prompt}. "
            f"Movement: {motion_prompt}. "
            f"Camera motion: {camera_motion}. "
            f"Cinematic lighting, high resolution, fluid motion, 30fps."
        )

        ar = aspect_ratio if aspect_ratio in ["16:9", "9:16", "1:1", "4:3", "21:9"] else "16:9"

        payload = {
            "prompt": full_prompt,
            "aspect_ratio": ar,
            "loop": False,
        }

        job_id = self._submit_generation(payload)
        return self._wait_and_download(job_id, out_path, duration, {"prompt": full_prompt}, **kwargs)

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

        if not image_path.exists():
            raise VideoGenerationError(self.name, f"Reference image file not found: {image_path}")

        # Luma accepts image URL or data URI
        img_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        data_uri = f"data:image/png;base64,{img_b64}"

        full_prompt = (
            f"{prompt}. "
            f"Motion: {motion_prompt}. "
            f"Maintain character visual consistency from the first frame."
        )

        payload = {
            "prompt": full_prompt,
            "keyframes": {
                "frame0": {
                    "type": "image",
                    "url": data_uri,
                }
            },
            "loop": False,
        }

        job_id = self._submit_generation(payload)
        return self._wait_and_download(job_id, out_path, duration, {"source_image": str(image_path)}, **kwargs)

    def _submit_generation(self, payload: Dict[str, Any]) -> str:
        req = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                job_id = data.get("id")
                if not job_id:
                    raise VideoGenerationError(self.name, f"No job ID in Luma response: {data}")
                return job_id
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else str(e)
            raise VideoGenerationError(self.name, f"Luma API error ({e.code}): {err_body}")
        except Exception as e:
            raise VideoGenerationError(self.name, str(e))

    def _wait_and_download(
        self,
        job_id: str,
        out_path: Path,
        duration: float,
        metadata: Dict[str, Any],
        **kwargs
    ) -> VideoJobResult:
        poll_url = f"{self.API_URL}/{job_id}"
        t0 = time.time()
        timeout = int(kwargs.get("timeout_seconds", 240))

        while time.time() - t0 < timeout:
            time.sleep(5)
            req = urllib.request.Request(poll_url, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                print(f"[{self.name}] Polling notice: {e}")
                continue

            state = data.get("state")
            if state == "completed":
                video_url = data.get("assets", {}).get("video")
                if not video_url:
                    raise VideoGenerationError(self.name, f"Job completed but no video URL found: {data}")

                urllib.request.urlretrieve(video_url, str(out_path))
                return VideoJobResult(
                    job_id=job_id,
                    status="completed",
                    provider=self.name,
                    video_url=video_url,
                    local_path=out_path,
                    duration=duration,
                    metadata={**metadata, "luma_id": job_id},
                )
            elif state in ["failed", "rejected"]:
                failure_reason = data.get("failure_reason") or "Unknown error"
                raise VideoGenerationError(self.name, f"Luma job {job_id} {state}: {failure_reason}")

        raise VideoGenerationError(self.name, f"Generation timed out after {timeout}s waiting for Luma Dream Machine.")
