import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import HOST, PORT, DEBUG, UPLOAD_DIR, OUTPUT_DIR, ALLOWED_ORIGINS, IS_PRODUCTION, USE_R2
from app.api.endpoints import router as api_router

app = FastAPI(
    title="AI Video Maker Agent API",
    description="Backend API for AI-powered automatic video creation from photos, clips, and music.",
    version="2.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
)

# ─── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ─── Static mounts (mount if not using Cloudflare R2) ──────────
if not USE_R2:
    app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
    app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")

# ─── API routes ───────────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "AI Video Maker Agent API",
        "version": "2.0.0",
        "environment": "production" if IS_PRODUCTION else "development",
        "endpoints": {
            "upload":     "/api/upload",
            "demo":       "/api/load-demo-assets",
            "create":     "/api/create-video",
            "status":     "/api/status/{task_id}",
            "result":     "/api/result/{task_id}",
            "regenerate": "/api/regenerate/{task_id}",
            "delete":     "/api/video/{task_id}",
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=DEBUG)
