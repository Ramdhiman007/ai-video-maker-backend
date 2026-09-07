import os
from typing import List, Dict, Any, Optional

from app.services.video_providers.base import (
    VideoGenerationProvider,
    ProviderNotConfiguredError,
)
from app.services.video_providers.veo_provider import GoogleVeoProvider
from app.services.video_providers.luma_provider import LumaDreamMachineProvider
from app.services.video_providers.runway_provider import RunwayGen3Provider
from app.services.video_providers.kling_provider import KlingVideoProvider
from app.services.video_providers.replicate_provider import ReplicateVideoProvider


ALL_PROVIDERS = [
    GoogleVeoProvider,
    LumaDreamMachineProvider,
    RunwayGen3Provider,
    KlingVideoProvider,
    ReplicateVideoProvider,
]


def get_available_providers() -> List[Dict[str, Any]]:
    """
    Returns a list of all registered AI video providers with their configuration status.
    Safe for frontend consumption (does not expose keys).
    """
    results = []
    for cls in ALL_PROVIDERS:
        instance = cls()
        results.append({
            "id": cls.__name__.lower().replace("provider", ""),
            "name": instance.name,
            "configured": instance.is_configured(),
            "required_env_vars": instance.get_required_env_vars(),
        })
    return results


def get_video_provider(provider_id: Optional[str] = None, api_key: Optional[str] = None) -> VideoGenerationProvider:
    """
    Resolves the active AI Video Generation Provider.
    If provider_id is passed, attempts to use that specific provider.
    Otherwise checks VIDEO_PROVIDER env var, or selects the first configured provider.
    Accepts an optional api_key passed directly from the user or request.
    """
    target = (provider_id or os.getenv("VIDEO_PROVIDER", "auto")).strip().lower()

    mapping = {
        "veo": GoogleVeoProvider,
        "google": GoogleVeoProvider,
        "google_veo": GoogleVeoProvider,
        "luma": LumaDreamMachineProvider,
        "lumalabs": LumaDreamMachineProvider,
        "runway": RunwayGen3Provider,
        "runwayml": RunwayGen3Provider,
        "kling": KlingVideoProvider,
        "klingai": KlingVideoProvider,
        "replicate": ReplicateVideoProvider,
    }

    if target in mapping:
        provider_instance = mapping[target](api_key=api_key)
        return provider_instance

    # If api_key provided in auto mode, use GoogleVeoProvider with this key
    if api_key:
        return GoogleVeoProvider(api_key=api_key)

    # 'auto': Find the first configured provider
    for cls in ALL_PROVIDERS:
        instance = cls(api_key=api_key)
        if instance.is_configured():
            return instance

    # Fallback to GoogleVeoProvider as default reference
    default_provider = GoogleVeoProvider(api_key=api_key)
    return default_provider

