import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from utils import PosterConfig, PosterDesigner, cleanup_directory

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/svg+xml"}

MAX_SHOP_NAME_LENGTH = 50
MAX_TAGLINE_LENGTH   = 100
MAX_UPI_ID_LENGTH    = 50

# ---------------------------------------------------------------------------
# Pydantic request model
# ---------------------------------------------------------------------------

class QRRequest(BaseModel):
    shop_name:    str           = Field(..., min_length=1, max_length=MAX_SHOP_NAME_LENGTH)
    upi_id:       str           = Field(..., min_length=3, max_length=MAX_UPI_ID_LENGTH)
    landing_url:  str           = Field(..., description="Unique URL created by Node.js")
    tagline:      Optional[str] = Field(None, max_length=MAX_TAGLINE_LENGTH)
    instagram:    Optional[str] = Field(None, max_length=30)
    website_url:  Optional[str] = Field(None, max_length=100)
    primary_color: str          = Field(default="#646cff")
    text_color:    str          = Field(default="#333333")

    @field_validator("primary_color", "text_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if not HEX_COLOR_PATTERN.match(v):
            raise ValueError("Invalid hex color format. Expected #RGB or #RRGGBB.")
        return v.lower()

    @field_validator("upi_id")
    @classmethod
    def validate_upi_id(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid UPI ID: must contain '@'.")
        return v.strip()

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class _AppState:
    generation_count: int = 0

app_state = _AppState()

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class SimpleRateLimiter:
    """Sliding-window in-memory rate limiter (not suitable for multi-process deployments)."""

    def __init__(self, limit: int = 20, window_seconds: int = 60) -> None:
        self.limit  = limit
        self.window = window_seconds
        self._history: dict[str, list[float]] = {}

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        window_start = now - self.window
        timestamps = [t for t in self._history.get(ip, []) if t > window_start]
        if len(timestamps) >= self.limit:
            return False
        timestamps.append(now)
        self._history[ip] = timestamps
        return True

limiter = SimpleRateLimiter()

# ---------------------------------------------------------------------------
# FastAPI app + middleware
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MYQR Poster Generator",
    description="Generates styled QR codes and A4 PDF posters for merchant landing pages.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Generation-Time"],
)
app.add_middleware(GZipMiddleware)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post(
    "/generate-pdf",
    summary="Generate QR code + PDF poster",
    response_description="A4 PDF poster as a downloadable file",
)
async def generate_assets(
    request:       Request,
    shop_name:     str           = Form(...,       description="Merchant / shop display name"),
    upi_id:        str           = Form(...,       description="Merchant UPI ID (must contain '@')"),
    landing_url:   str           = Form(...,       description="Unique landing-page URL from Node.js"),
    primary_color: str           = Form("#646cff", description="Hex colour for header and QR dots"),
    text_color:    str           = Form("#333333", description="Hex colour for body text"),
    tagline:       Optional[str] = Form(None,      description="Optional one-line tagline"),
    instagram:     Optional[str] = Form(None,      description="Optional Instagram handle"),
    website_url:   Optional[str] = Form(None,      description="Optional website URL"),
    logo:          Optional[UploadFile] = File(None, description="Optional merchant logo (PNG/JPG/WebP/SVG)"),
) -> FileResponse:
    """
    Called by the Node.js backend.

    1. Validates the request via Pydantic.
    2. Saves the uploaded logo (if any) to a temp directory.
    3. Generates a styled QR code pointing to `landing_url`.
    4. Composes an A4 PDF poster and streams it to the caller.
    5. Cleans up all temp files via a background task *after* the stream completes.
    """
    client_ip = request.client.host
    if not limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait and try again.")

    start_time = time.time()
    work_dir   = tempfile.mkdtemp(prefix="myqr_")
    logo_path: Optional[str] = None

    try:
        # Validate all fields together via Pydantic
        req = QRRequest(
            shop_name=shop_name,
            upi_id=upi_id,
            landing_url=landing_url,
            primary_color=primary_color,
            text_color=text_color,
            tagline=tagline,
            instagram=instagram,
            website_url=website_url,
        )

        logo_path = await _save_logo(logo, work_dir)

        config = PosterConfig(
            shop_name=req.shop_name,
            upi_id=req.upi_id,
            landing_url=req.landing_url,
            primary_color=req.primary_color,
            text_color=req.text_color,
            tagline=req.tagline or "",
            instagram=req.instagram or "",
            website_url=req.website_url or "",
            logo_path=logo_path,
        )

        designer  = PosterDesigner(work_dir)
        pdf_path  = designer.generate_poster(config)
        gen_time  = time.time() - start_time
        app_state.generation_count += 1

        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", req.shop_name)
        filename  = f"{safe_name}_QR.pdf"
        logger.info("Returning poster '%s' (%.2fs)", filename, gen_time)

        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=filename,
            # BackgroundTask runs AFTER the response is fully streamed — safe to delete
            background=BackgroundTask(_deferred_cleanup, work_dir),
            headers={"X-Generation-Time": f"{gen_time:.2f}s"},
        )

    except HTTPException:
        cleanup_directory(work_dir)   # immediate cleanup on known errors
        raise
    except Exception as exc:
        cleanup_directory(work_dir)
        logger.exception("Poster generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.get("/", summary="Welcome endpoint")
async def root() -> dict:
    return {"message": "Welcome to the MYQR Poster Generator API!"}

@app.get("/health", summary="Service health check")
async def health() -> dict:
    return {
        "status":  "healthy",
        "version": app.version,
        "count":   app_state.generation_count,
    }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _save_logo(upload: Optional[UploadFile], work_dir: str) -> Optional[str]:
    """Validate, persist, and return the path for an uploaded logo file."""
    if upload is None or not upload.filename:
        return None

    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported logo format '{upload.content_type}'. "
                   f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    ext        = os.path.splitext(upload.filename)[1] or ".png"
    logo_path  = os.path.join(work_dir, f"logo{ext}")

    with open(logo_path, "wb") as fh:
        shutil.copyfileobj(upload.file, fh)

    logger.debug("Logo saved → %s (%s)", logo_path, upload.content_type)
    return logo_path


async def _deferred_cleanup(work_dir: str, delay: float = 20.0) -> None:
    """Wait briefly (so the file stream can finish), then remove the temp directory."""
    await asyncio.sleep(delay)
    cleanup_directory(work_dir)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)