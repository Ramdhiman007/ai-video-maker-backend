from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List


class ProviderNotConfiguredError(Exception):
    """Raised when an AI Video Provider is selected but required credentials are missing."""
    def __init__(self, message_or_name: str = "AI Video Provider is not configured. Please configure the required API key.", required_vars: Optional[List[str]] = None):
        if required_vars:
            vars_str = ", ".join(required_vars)
            msg = f"AI Video Provider '{message_or_name}' is not configured. Please configure the required API key: {vars_str}"
            self.provider_name = message_or_name
            self.required_vars = required_vars
        else:
            msg = message_or_name
            self.provider_name = ""
            self.required_vars = []
        super().__init__(msg)


class VideoGenerationError(Exception):
    """Raised when an AI Video Generation job fails."""
    def __init__(self, provider_name: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(f"[{provider_name}] AI Video Generation Failed: {message}")
        self.provider_name = provider_name
        self.details = details or {}


@dataclass
class VideoJobResult:
    """Represents the status and output of an AI video generation job."""
    job_id: str
    status: str  # 'queued', 'processing', 'completed', 'failed', 'cancelled'
    provider: str
    video_url: Optional[str] = None
    local_path: Optional[Path] = None
    duration: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class VideoGenerationProvider(ABC):
    """
    Abstract Base Class for Real AI Video Generation Providers.
    Supports both Text-to-Video and Image-to-Video generation pipelines.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'Google Veo', 'Luma Dream Machine')."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if the required API keys and configuration exist."""
        pass

    def is_available(self) -> bool:
        """Alias for is_configured()."""
        return self.is_configured()

    @abstractmethod
    def get_required_env_vars(self) -> List[str]:
        """Returns the list of environment variables required to configure this provider."""
        pass

    @abstractmethod
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
        """
        Generates genuine video from a text prompt and explicit motion prompt.
        Must produce an actual moving video file saved to out_path.
        """
        pass

    @abstractmethod
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
        """
        Uses an initial reference image as the starting frame to generate
        genuine temporal motion (image-to-video).
        """
        pass

    def check_status(self, job_id: str) -> VideoJobResult:
        """
        Checks the status of an asynchronous video generation job.
        Default implementation returns completed if synchronous.
        """
        raise NotImplementedError("Asynchronous job status polling not implemented for this provider.")
