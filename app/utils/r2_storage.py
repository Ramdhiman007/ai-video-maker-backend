"""
Cloudflare R2 Storage Utility
S3-compatible object storage via boto3.
Handles: upload, download-to-temp, delete, and pre-signed URL generation.
"""

import os
import uuid
import boto3
from pathlib import Path
from typing import Optional
from botocore.client import Config
from botocore.exceptions import ClientError


def _get_r2_client():
    """Creates and returns a boto3 S3 client configured for Cloudflare R2."""
    account_id = os.environ["R2_ACCOUNT_ID"]
    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _bucket() -> str:
    return os.environ.get("R2_BUCKET_NAME", "ai-video-maker")


def _public_url_base() -> str:
    """Returns the public R2 CDN base URL (e.g. https://pub-xxx.r2.dev)."""
    return os.environ.get("R2_PUBLIC_URL", "").rstrip("/")


# ─── Upload ──────────────────────────────────────────────────────────────────

def upload_file(local_path: Path, r2_key: str, content_type: str = "application/octet-stream") -> str:
    """
    Uploads a local file to R2.
    Returns the public URL for the file.
    """
    client = _get_r2_client()
    client.upload_file(
        Filename=str(local_path),
        Bucket=_bucket(),
        Key=r2_key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{_public_url_base()}/{r2_key}"


def upload_bytes(data: bytes, r2_key: str, content_type: str = "application/octet-stream") -> str:
    """
    Uploads raw bytes to R2.
    Returns the public URL for the object.
    """
    client = _get_r2_client()
    client.put_object(
        Bucket=_bucket(),
        Key=r2_key,
        Body=data,
        ContentType=content_type,
    )
    return f"{_public_url_base()}/{r2_key}"


def upload_media_file(local_path: Path, job_id: str, original_filename: str) -> tuple[str, str]:
    """
    Uploads a media file under uploads/{job_id}/{uuid}_{filename}.
    Returns (r2_key, public_url).
    """
    ext = Path(original_filename).suffix.lower()
    uid = uuid.uuid4().hex[:8]
    r2_key = f"uploads/{job_id}/{uid}_{original_filename}"

    content_type_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".avi": "video/x-msvideo", ".webm": "video/webm",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    }
    content_type = content_type_map.get(ext, "application/octet-stream")
    url = upload_file(local_path, r2_key, content_type)
    return r2_key, url


def upload_output_video(local_path: Path, task_id: str) -> str:
    """
    Uploads a rendered MP4 to outputs/{task_id}.mp4.
    Returns the public URL.
    """
    r2_key = f"outputs/video_{task_id}.mp4"
    return upload_file(local_path, r2_key, "video/mp4")


# ─── Download ────────────────────────────────────────────────────────────────

def download_file(r2_key: str, local_dest: Path) -> Path:
    """
    Downloads an R2 object to a local temp path.
    Returns the local path.
    """
    local_dest.parent.mkdir(parents=True, exist_ok=True)
    client = _get_r2_client()
    client.download_file(Bucket=_bucket(), Key=r2_key, Filename=str(local_dest))
    return local_dest


# ─── Delete ──────────────────────────────────────────────────────────────────

def delete_file(r2_key: str) -> bool:
    """Deletes an object from R2. Returns True on success."""
    try:
        client = _get_r2_client()
        client.delete_object(Bucket=_bucket(), Key=r2_key)
        return True
    except ClientError:
        return False


def delete_job_files(job_id: str) -> int:
    """Deletes all R2 objects under uploads/{job_id}/. Returns number deleted."""
    client = _get_r2_client()
    prefix = f"uploads/{job_id}/"
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=_bucket(), Key=obj["Key"])
            deleted += 1
    return deleted


# ─── Signed URLs ─────────────────────────────────────────────────────────────

def get_presigned_download_url(r2_key: str, expires_in: int = 3600) -> str:
    """
    Generates a time-limited pre-signed download URL (default 1 hour).
    Use this when R2 bucket is private.
    """
    client = _get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": r2_key},
        ExpiresIn=expires_in,
    )


# ─── Availability Check ───────────────────────────────────────────────────────

def is_r2_configured() -> bool:
    """Returns True if all required R2 env vars are set."""
    required = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"]
    return all(os.environ.get(k) for k in required)
