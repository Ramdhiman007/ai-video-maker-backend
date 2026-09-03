import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present (local development)
load_dotenv()

# ─── Environment ──────────────────────────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT == "production"

# ─── Base directories (used in local dev / worker temp space) ─────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

UPLOAD_DIR = BASE_DIR / os.getenv("UPLOAD_DIR", "uploads")
OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "outputs")
TEMP_DIR   = BASE_DIR / os.getenv("TEMP_DIR", "temp")

try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"[config] Directory creation notice: {e}")

# ─── AI Keys ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ─── FFmpeg ───────────────────────────────────────────────────────────────────
FFMPEG_PATH  = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")

# ─── Server ───────────────────────────────────────────────────────────────────
HOST  = os.getenv("HOST", "0.0.0.0")
PORT  = int(os.getenv("PORT", "8000"))
DEBUG = not IS_PRODUCTION and os.getenv("DEBUG", "True").lower() in ("true", "1", "t")

# ─── CORS ─────────────────────────────────────────────────────────────────────
# Comma-separated list of allowed origins.
# Example: https://www.ramtechnicalhelp.com,https://ramtechnicalhelp.com
_cors_raw = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS: list[str] = (
    ["*"] if _cors_raw.strip() == "*"
    else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)
# Allow local preview server to test against Railway backend
for _loc in ["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:3000"]:
    if "*" not in ALLOWED_ORIGINS and _loc not in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.append(_loc)

# ─── Redis ────────────────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "")

# ─── Cloudflare R2 ────────────────────────────────────────────────────────────
R2_ACCOUNT_ID      = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID   = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME     = os.getenv("R2_BUCKET_NAME", "ai-video-maker")
R2_PUBLIC_URL      = os.getenv("R2_PUBLIC_URL", "")  # e.g. https://pub-xxx.r2.dev

USE_R2 = bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY)

# ─── Upload limits ────────────────────────────────────────────────────────────
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# ─── Video expiry ─────────────────────────────────────────────────────────────
VIDEO_EXPIRY_HOURS = int(os.getenv("VIDEO_EXPIRY_HOURS", "24"))
