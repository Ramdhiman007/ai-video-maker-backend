"""
Video Generation Providers Package
Abstract video provider interface and concrete adapters for AI video generation services:
- Google Veo (via google-genai)
- Luma Dream Machine
- Runway Gen-3 Alpha
- Kling AI
- Replicate (Minimax, Stable Video Diffusion)
"""

from app.services.video_providers.base import (
    VideoGenerationProvider,
    VideoJobResult,
    ProviderNotConfiguredError,
    VideoGenerationError,
)
from app.services.video_providers.factory import get_video_provider, get_available_providers

__all__ = [
    "VideoGenerationProvider",
    "VideoJobResult",
    "ProviderNotConfiguredError",
    "VideoGenerationError",
    "get_video_provider",
    "get_available_providers",
]
