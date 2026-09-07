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


class ReplicateVideoProvider(VideoGenerationProvider):
    """
    Adapter for Replicate Video Models (e.g. Minimax Video-01, Stable Video Diffusion).
    """

    BASE_URL = "https://api.replicate.com/v1/predictions"

    def __init__(self, api_token: Optional[str] = None, api_key: Optional[str] = None, model: str = "minimax/video-01"):
        self._api_token = (
            api_token
            or api_key
            or os.getenv("REPLICATE_API_TOKEN")
            or os.getenv("REPLICATE_API_KEY")
            or os.getenv("VIDEO_PROVIDER_API_KEY")
            or ""
        )
        self.model = os.getenv("REPLICATE_VIDEO_MODEL", model)


    @property
    def name(self) -> str:
        return f"Replicate ({self.model})"

    def is_configured(self) -> bool:
        return bool(self._api_token and len(self._api_token.strip()) > 10)

    def get_required_env_vars(self) -> List[str]:
        return ["REPLICATE_API_TOKEN", "VIDEO_PROVIDER_API_KEY"]

    def _ensure_configured(self):
        if not self.is_configured():
            raise ProviderNotConfiguredError("Replicate", self.get_required_env_vars())

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Token {self._api_token}",
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

        full_prompt = f"{prompt}. Motion: {motion_prompt}. Camera: {camera_motion}."
        payload = {
            "version": kwargs.get("version", "minimax/video-01"),
            "input": {
                "prompt": full_prompt,
                "prompt_optimizer": True,
            },
        }

        # If model is in owner/name format, use predictions with model parameter
        body_data = {"input": {"prompt": full_prompt}}
        if "/" in self.model:
            url = f"https://api.replicate.com/v1/models/{self.model}/predictions"
        else:
            url = self.BASE_URL
            body_data["version"] = self.model

        req = urllib.request.Request(
            url,
            data=json.dumps(body_data).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                prediction_id = data.get("id")
                if not prediction_id:
                    raise VideoGenerationError(self.name, f"Replicate returned no prediction ID: {data}")
        except Exception as e:
            raise VideoGenerationError(self.name, str(e))

        return self._poll_prediction(prediction_id, out_path, duration, {"prompt": full_prompt}, **kwargs)

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
        data_uri = f"data:image/png;base64,{img_b64}"

        body_data = {
            "input": {
                "first_frame_image": data_uri,
                "prompt": f"{prompt}. Motion: {motion_prompt}.",
            }
        }
        url = f"https://api.replicate.com/v1/models/{self.model}/predictions"
        req = urllib.request.Request(
            url,
            data=json.dumps(body_data).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                prediction_id = data.get("id")
        except Exception as e:
            raise VideoGenerationError(self.name, str(e))

        return self._poll_prediction(prediction_id, out_path, duration, {"source_image": str(image_path)}, **kwargs)

    def _poll_prediction(
        self,
        prediction_id: str,
        out_path: Path,
        duration: float,
        metadata: Dict[str, Any],
        **kwargs
    ) -> VideoJobResult:
        poll_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
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

            status = data.get("status")
            if status == "succeeded":
                output = data.get("output")
                video_url = output if isinstance(output, str) else (output[0] if isinstance(output, list) else None)
                if not video_url:
                    raise VideoGenerationError(self.name, f"Replicate succeeded but output is invalid: {output}")
                urllib.request.urlretrieve(video_url, str(out_path))
                return VideoJobResult(
                    job_id=prediction_id,
                    status="completed",
                    provider=self.name,
                    video_url=video_url,
                    local_path=out_path,
                    duration=duration,
                    metadata=metadata,
                )
            elif status in ["failed", "canceled"]:
                err = data.get("error", "Prediction failed")
                raise VideoGenerationError(self.name, f"Replicate job {prediction_id} {status}: {err}")

        raise VideoGenerationError(self.name, f"Generation timed out after {timeout}s waiting for Replicate.")
