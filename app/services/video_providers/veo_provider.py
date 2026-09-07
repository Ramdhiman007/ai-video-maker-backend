import os
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.services.video_providers.base import (
    VideoGenerationProvider,
    VideoJobResult,
    ProviderNotConfiguredError,
    VideoGenerationError,
)


class GoogleVeoProvider(VideoGenerationProvider):
    """
    Adapter for Google Veo video generation model (Veo 2.0 / Veo 3.0)
    using the official google-genai Python SDK.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "veo-2.0-generate-001"):
        self._api_key = (
            api_key
            or os.getenv("VEO_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("VIDEO_PROVIDER_API_KEY")
            or ""
        )
        self.model = os.getenv("VEO_MODEL", model)

    @property
    def name(self) -> str:
        return f"Google Veo ({self.model})"

    def is_configured(self) -> bool:
        return bool(self._api_key and len(self._api_key.strip()) > 10)

    def get_required_env_vars(self) -> List[str]:
        return ["VEO_API_KEY", "GEMINI_API_KEY", "VIDEO_PROVIDER_API_KEY"]

    def _ensure_configured(self):
        if not self.is_configured():
            raise ProviderNotConfiguredError("Google Veo", self.get_required_env_vars())

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
            f"Motion: {motion_prompt}. "
            f"Camera: {camera_motion}. "
            f"High quality cinematic 1080p, natural fluid motion, continuous frame rate."
        )

        ar = "16:9"
        if aspect_ratio in ["9:16", "1:1", "16:9"]:
            ar = aspect_ratio

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self._api_key)
            operation = client.models.generate_videos(
                model=self.model,
                prompt=full_prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio=ar,
                    number_of_videos=1,
                    duration_seconds=min(max(int(duration), 5), 10),
                ),
            )

            job_id = getattr(operation, "name", f"veo_{int(time.time())}")
            t0 = time.time()
            max_wait_seconds = int(kwargs.get("timeout_seconds", 180))

            while not operation.done:
                if time.time() - t0 > max_wait_seconds:
                    raise VideoGenerationError(
                        self.name,
                        f"Generation timed out after {max_wait_seconds}s waiting for Google Veo."
                    )
                time.sleep(5)
                operation = client.operations.get(operation)

            if getattr(operation, "error", None):
                raise VideoGenerationError(self.name, f"Veo operation error: {operation.error}")

            res = operation.response
            if not res or not getattr(res, "generated_videos", None):
                raise VideoGenerationError(self.name, "Veo returned no generated videos in response.")

            video_obj = res.generated_videos[0]
            # Write video bytes or download from URI
            if hasattr(video_obj, "video") and hasattr(video_obj.video, "video_bytes") and video_obj.video.video_bytes:
                out_path.write_bytes(video_obj.video.video_bytes)
            elif hasattr(video_obj, "video") and hasattr(video_obj.video, "uri") and video_obj.video.uri:
                urllib.request.urlretrieve(video_obj.video.uri, str(out_path))
            else:
                # Direct download method on video object if supported
                client.files.download(file=video_obj.video, destination=str(out_path))

            return VideoJobResult(
                job_id=job_id,
                status="completed",
                provider=self.name,
                local_path=out_path,
                duration=duration,
                metadata={"model": self.model, "prompt": full_prompt},
            )
        except Exception as e:
            if isinstance(e, (ProviderNotConfiguredError, VideoGenerationError)):
                raise e
            raise VideoGenerationError(self.name, str(e))

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
            raise VideoGenerationError(self.name, f"Reference image not found: {image_path}")

        full_prompt = (
            f"Animate this scene: {prompt}. "
            f"Motion: {motion_prompt}. "
            f"Preserve character appearance and environment from the initial image, bringing genuine temporal movement."
        )

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self._api_key)
            img_bytes = image_path.read_bytes()
            img_type = types.Image(image_bytes=img_bytes)

            operation = client.models.generate_videos(
                model=self.model,
                prompt=full_prompt,
                image=img_type,
                config=types.GenerateVideosConfig(
                    number_of_videos=1,
                    duration_seconds=min(max(int(duration), 5), 10),
                ),
            )

            job_id = getattr(operation, "name", f"veo_i2v_{int(time.time())}")
            t0 = time.time()
            max_wait_seconds = int(kwargs.get("timeout_seconds", 180))

            while not operation.done:
                if time.time() - t0 > max_wait_seconds:
                    raise VideoGenerationError(
                        self.name,
                        f"Generation timed out after {max_wait_seconds}s waiting for Google Veo image-to-video."
                    )
                time.sleep(5)
                operation = client.operations.get(operation)

            if getattr(operation, "error", None):
                raise VideoGenerationError(self.name, f"Veo operation error: {operation.error}")

            res = operation.response
            if not res or not getattr(res, "generated_videos", None):
                raise VideoGenerationError(self.name, "Veo returned no generated videos in response.")

            video_obj = res.generated_videos[0]
            if hasattr(video_obj, "video") and hasattr(video_obj.video, "video_bytes") and video_obj.video.video_bytes:
                out_path.write_bytes(video_obj.video.video_bytes)
            elif hasattr(video_obj, "video") and hasattr(video_obj.video, "uri") and video_obj.video.uri:
                urllib.request.urlretrieve(video_obj.video.uri, str(out_path))
            else:
                client.files.download(file=video_obj.video, destination=str(out_path))

            return VideoJobResult(
                job_id=job_id,
                status="completed",
                provider=self.name,
                local_path=out_path,
                duration=duration,
                metadata={"model": self.model, "source_image": str(image_path)},
            )
        except Exception as e:
            if isinstance(e, (ProviderNotConfiguredError, VideoGenerationError)):
                raise e
            raise VideoGenerationError(self.name, str(e))
