import os
import secrets
from pathlib import Path
from io import BytesIO

import boto3
from botocore.client import Config
import barcode
from barcode.writer import ImageWriter

from fastapi import (
    FastAPI,
    File,
    Form,
    Request,
    UploadFile,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from supabase import create_client, Client


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

APP_SECRET = os.environ["APP_SECRET"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

# Cloudflare R2
R2_ENDPOINT = os.environ["R2_ENDPOINT"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]

# Public URL of your Render application
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(title="Permanent Barcode Video")

app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    https_only=True,
    same_site="lax",
)

templates = Jinja2Templates(directory="templates")


# ============================================================
# CLOUDFLARE R2 CLIENT
# ============================================================

r2 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)


# ============================================================
# SUPABASE CLIENT
# ============================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY,
)


# ============================================================
# CONSTANTS
# ============================================================

STATE_KEY = "current_video"


# ============================================================
# SUPABASE STATE FUNCTIONS
# ============================================================

def get_current_key():
    """
    Get the currently active video key from Supabase.
    """

    try:
        response = (
            supabase
            .table("app_state")
            .select("value")
            .eq("key", STATE_KEY)
            .limit(1)
            .execute()
        )

        if response.data:
            value = response.data[0].get("value")

            if value:
                return value

        return None

    except Exception as exc:
        print(f"Supabase get_current_key error: {exc}")
        return None


def set_current_key(key: str):
    """
    Store the currently active video key in Supabase.
    """

    try:
        (
            supabase
            .table("app_state")
            .upsert(
                {
                    "key": STATE_KEY,
                    "value": key,
                },
                on_conflict="key",
            )
            .execute()
        )

    except Exception as exc:
        print(f"Supabase set_current_key error: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Unable to save video state.",
        )


def clear_current_key():
    """
    Remove the currently active video from Supabase.
    """

    try:
        (
            supabase
            .table("app_state")
            .delete()
            .eq("key", STATE_KEY)
            .execute()
        )

    except Exception as exc:
        print(f"Supabase clear_current_key error: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Unable to clear video state.",
        )


# ============================================================
# R2 VIDEO FUNCTIONS
# ============================================================

def delete_r2_video(key: str):
    """
    Delete a video from Cloudflare R2.
    """

    if not key:
        return

    try:
        r2.delete_object(
            Bucket=R2_BUCKET,
            Key=key,
        )

    except Exception as exc:
        print(f"R2 delete error for {key}: {exc}")


def upload_to_r2(video: UploadFile, key: str):
    """
    Upload an uploaded video directly to Cloudflare R2.
    """

    try:
        r2.upload_fileobj(
            video.file,
            R2_BUCKET,
            key,
            ExtraArgs={
                "ContentType": video.content_type or "video/mp4"
            },
        )

    except Exception as exc:
        print(f"R2 upload error: {exc}")

        raise HTTPException(
            status_code=500,
            detail="Unable to upload video.",
        )


def generate_video_url(key: str):
    """
    Generate a temporary signed URL for the video.
    """

    try:
        return r2.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": R2_BUCKET,
                "Key": key,
            },
            ExpiresIn=3600,
        )

    except Exception as exc:
        print(f"R2 presigned URL error: {exc}")

        raise HTTPException(
            status_code=500,
            detail="Unable to generate video URL.",
        )


# ============================================================
# ADMIN AUTHENTICATION
# ============================================================

def is_admin(request: Request):
    return request.session.get("admin") is True


# ============================================================
# HOME
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return RedirectResponse(
        "/video",
        status_code=302,
    )


# ============================================================
# PUBLIC VIDEO PAGE
# ============================================================

@app.get("/video", response_class=HTMLResponse)
async def video_page(request: Request):

    current_key = get_current_key()

    if not current_key:
        return templates.TemplateResponse(
            "video.html",
            {
                "request": request,
                "video_url": None,
                "message": "No video is available right now.",
            },
        )

    video_url = generate_video_url(current_key)

    return templates.TemplateResponse(
        "video.html",
        {
            "request": request,
            "video_url": video_url,
            "message": None,
        },
    )


# ============================================================
# ADMIN PAGE
# ============================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):

    if not is_admin(request):
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": None,
            },
        )

    current_key = get_current_key()

    public_video_url = (
        f"{PUBLIC_URL}/video"
        if PUBLIC_URL
        else "/video"
    )

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "current": current_key,
            "public_url": public_video_url,
            "error": None,
        },
    )


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.post("/admin/login")
async def admin_login(
    request: Request,
    password: str = Form(...),
):

    if not secrets.compare_digest(
        password,
        ADMIN_PASSWORD,
    ):
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": "Incorrect password.",
            },
            status_code=401,
        )

    request.session["admin"] = True

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# ADMIN VIDEO UPLOAD
# ============================================================

@app.post("/admin/upload")
async def upload_video(
    request: Request,
    video: UploadFile = File(...),
):

    if not is_admin(request):
        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    if not video.filename:
        raise HTTPException(
            status_code=400,
            detail="Choose a video.",
        )

    # Supported video formats
    allowed_extensions = {
        ".mp4",
        ".webm",
        ".mov",
        ".m4v",
    }

    extension = Path(
        video.filename
    ).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Use MP4, WebM, MOV or M4V.",
        )

    # Get the currently active video
    old_key = get_current_key()

    # Generate a random R2 filename
    new_key = (
        f"videos/{secrets.token_hex(16)}{extension}"
    )

    # Upload new video to R2
    upload_to_r2(
        video,
        new_key,
    )

    # Save new video as current video in Supabase
    try:
        set_current_key(new_key)

    except Exception:
        # If Supabase fails, remove the newly uploaded file
        # so we don't leave an unused video in R2.
        delete_r2_video(new_key)
        raise

    # Delete previous video after the new state is saved
    if old_key and old_key != new_key:
        delete_r2_video(old_key)

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# ADMIN DELETE CURRENT VIDEO
# ============================================================

@app.post("/admin/delete")
async def delete_video(request: Request):

    if not is_admin(request):
        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    current_key = get_current_key()

    # Remove state from Supabase first
    if current_key:
        clear_current_key()

        # Remove actual video from R2
        delete_r2_video(current_key)

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# ADMIN LOGOUT
# ============================================================

@app.post("/admin/logout")
async def logout(request: Request):

    request.session.clear()

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# BARCODE IMAGE
# ============================================================

@app.get("/admin/barcode")
async def barcode_image(request: Request):

    if not is_admin(request):
        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    if not PUBLIC_URL:
        raise HTTPException(
            status_code=500,
            detail="Set PUBLIC_URL first.",
        )

    # The barcode ALWAYS points to this permanent URL.
    target = f"{PUBLIC_URL}/video"

    # Generate Code 128 barcode
    code = barcode.get(
        "code128",
        target,
        writer=ImageWriter(),
    )

    buffer = BytesIO()

    code.write(
        buffer,
        options={
            "module_width": 0.25,
            "module_height": 18,
            "font_size": 9,
            "text_distance": 4,
            "quiet_zone": 6,
            "write_text": True,
        },
    )

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/png",
    )











