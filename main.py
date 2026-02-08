from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, validator, Field
from starlette.background import BackgroundTask # <--- Or this
from typing import Optional
import os
import re
import uuid
import logging
from pathlib import Path
import shutil
import time
import asyncio

# Import the refactored generator from your utils.py
from utils import PosterDesigner

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Constants ---
HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/svg+xml"]
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_SHOP_NAME_LENGTH = 50
MAX_TAGLINE_LENGTH = 100
MAX_UPI_ID_LENGTH = 50

# --- Pydantic Validation Model ---
class QRRequest(BaseModel):
    shop_name: str = Field(..., min_length=1, max_length=MAX_SHOP_NAME_LENGTH)
    upi_id: str = Field(..., min_length=3, max_length=MAX_UPI_ID_LENGTH)
    tagline: Optional[str] = Field(None, max_length=MAX_TAGLINE_LENGTH)
    primary_color: str = Field(default="#646cff")
    text_color: str = Field(default="#000000")
    
    @validator("primary_color", "text_color")
    def validate_hex_color(cls, v):
        if not HEX_COLOR_PATTERN.match(v):
            raise ValueError("Invalid hex color format")
        return v.lower()
    
    @validator("upi_id")
    def validate_upi_id(cls, v):
        if "@" not in v:
            raise ValueError("Invalid UPI ID: Must contain '@'")
        return v.strip()

# --- App State & Utilities ---
class AppState:
    def __init__(self):
        self.generation_count = 0

app_state = AppState()
app = FastAPI(title="QR Generator API")

# Rate Limiter (Simple Memory-based)
class SimpleRateLimiter:
    def __init__(self, limit=20, window=60):
        self.limit = limit
        self.window = window
        self.history = {}

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        if ip not in self.history: self.history[ip] = []
        self.history[ip] = [t for t in self.history[ip] if now - t < self.window]
        if len(self.history[ip]) >= self.limit: return False
        self.history[ip].append(now)
        return True

limiter = SimpleRateLimiter()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Generation-Time"]
)
app.add_middleware(GZipMiddleware)

# --- Helper Functions ---
async def save_temp_logo(logo: UploadFile) -> Optional[str]:
    if not logo or not logo.filename: return None
    if logo.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid logo format")
    
    temp_dir = Path("temp_uploads")
    temp_dir.mkdir(exist_ok=True)
    file_path = temp_dir / f"{uuid.uuid4().hex}_{logo.filename}"
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(logo.file, buffer)
    return str(file_path)

async def cleanup_task(pdf_path: str, logo_path: Optional[str], designer_temp: str):
    """Clean up files after a delay to ensure the download completes"""
    await asyncio.sleep(20) # Give the user 20 seconds to finish the download
    try:
        # 1. Remove the PDF file
        if pdf_path and os.path.exists(pdf_path): 
            os.remove(pdf_path)
            
        # 2. Remove the uploaded logo
        if logo_path and os.path.exists(logo_path): 
            os.remove(logo_path)
            
        # 3. CRITICAL: Remove the temporary folder where the QR pieces were stored
        if designer_temp and os.path.exists(designer_temp):
            shutil.rmtree(designer_temp, ignore_errors=True)
            
        logger.info(f"Disk Space Saved: Temporary session files cleared.")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# --- API Endpoints ---
@app.post("/generate-pdf")
async def generate_pdf_endpoint(
    request: Request,
    shop_name: str = Form(...),
    upi_id: str = Form(...),
    tagline: Optional[str] = Form(None),
    primary_color: str = Form("#646cff"),
    text_color: str = Form("#000000"),
    logo: Optional[UploadFile] = File(None)
):
    # 1. Rate Limiting
    client_ip = request.client.host
    if not limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    start_time = time.time()
    logo_path = None
    
    try:
        # 2. Validation
        req_data = QRRequest(
            shop_name=shop_name, upi_id=upi_id, tagline=tagline,
            primary_color=primary_color, text_color=text_color
        )
        
        # 3. Save Logo
        logo_path = await save_temp_logo(logo)
        
        # 4. Generate PDF
        designer = PosterDesigner()
        pdf_path = designer.generate_poster(
            shop_name=req_data.shop_name,
            upi_id=req_data.upi_id,
            tagline=req_data.tagline or "",
            primary_color=req_data.primary_color,
            text_color=req_data.text_color,
            logo_path=logo_path
        )
        
        gen_time = time.time() - start_time
        app_state.generation_count += 1

        # 5. Return Response
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', req_data.shop_name)
        
        return FileResponse(
    path=pdf_path,
    filename=f"{safe_name}_QR.pdf",
    media_type="application/pdf",
    # CORRECT: BackgroundTask is a reference that FastAPI will execute AFTER sending the file
    background=BackgroundTask(cleanup_task, pdf_path, logo_path, designer.temp_dir),
    headers={"X-Generation-Time": f"{gen_time:.2f}s"}
     )

    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        if logo_path and os.path.exists(logo_path): os.remove(logo_path)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy", "count": app_state.generation_count}

if __name__ == "__main__":
    import uvicorn
    # Render provides a 'PORT' environment variable. If it doesn't exist, we default to 8000.
    port = int(os.environ.get("PORT", 8000))
    # We use 0.0.0.0 so it's accessible externally on Render
    uvicorn.run(app, host="0.0.0.0", port=port)