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
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from supabase import create_client, Client


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

APP_SECRET = os.environ["APP_SECRET"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

# ------------------------------------------------------------
# Cloudflare R2
# ------------------------------------------------------------

R2_ENDPOINT = os.environ["R2_ENDPOINT"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]

# ------------------------------------------------------------
# Public URL
# ------------------------------------------------------------

PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# ------------------------------------------------------------
# Supabase
# ------------------------------------------------------------

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Permanent Barcode Video"
)


# ============================================================
# SESSION MIDDLEWARE
# ============================================================

app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    https_only=True,
    same_site="lax",
)


# ============================================================
# TEMPLATES
# ============================================================

templates = Jinja2Templates(
    directory="templates"
)


# ============================================================
# CLOUDFLARE R2 CLIENT
# ============================================================

r2 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(
        signature_version="s3v4"
    ),
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

# This is the single row/key used to remember
# which video is currently active.

STATE_KEY = "current_video"


# ============================================================
# SUPABASE STATE FUNCTIONS
# ============================================================

def get_current_key():
    """
    Get the currently active video key from Supabase.

    Returns:
        str | None
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
        print(
            f"Supabase get_current_key error: {exc}"
        )

        # Do not expose database errors to the visitor.
        raise HTTPException(
            status_code=500,
            detail="Unable to read video state.",
        )


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
        print(
            f"Supabase set_current_key error: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to save video state.",
        )


def clear_current_key():
    """
    Remove the currently active video key
    from Supabase.
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
        print(
            f"Supabase clear_current_key error: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to clear video state.",
        )


# ============================================================
# CLOUDFLARE R2 FUNCTIONS
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
        print(
            f"R2 delete error for {key}: {exc}"
        )


def upload_to_r2(
    video: UploadFile,
    key: str,
):
    """
    Upload an uploaded video to Cloudflare R2.
    """

    try:
        r2.upload_fileobj(
            video.file,
            R2_BUCKET,
            key,
            ExtraArgs={
                "ContentType": (
                    video.content_type
                    or "video/mp4"
                )
            },
        )

    except Exception as exc:
        print(
            f"R2 upload error: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to upload video.",
        )


def generate_video_url(key: str):
    """
    Generate a temporary signed URL
    for the video stored in R2.
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
        print(
            f"R2 presigned URL error: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to generate video URL.",
        )


# ============================================================
# ADMIN AUTHENTICATION
# ============================================================

def is_admin(request: Request):
    """
    Check whether the current session
    belongs to an authenticated admin.
    """

    return request.session.get("admin") is True


# ============================================================
# HOME
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def home():

    return RedirectResponse(
        "/video",
        status_code=302,
    )


# ============================================================
# PUBLIC VIDEO PAGE
# ============================================================

@app.get(
    "/video",
    response_class=HTMLResponse,
)
async def video_page(
    request: Request,
):

    current_key = get_current_key()

    # No active video
    if not current_key:

        return templates.TemplateResponse(
            "video.html",
            {
                "request": request,
                "video_url": None,
                "message": (
                    "No video is available right now."
                ),
            },
        )

    # Generate temporary R2 URL
    video_url = generate_video_url(
        current_key
    )

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

@app.get(
    "/admin",
    response_class=HTMLResponse,
)
async def admin_page(
    request: Request,
):

    # Show login page if not authenticated
    if not is_admin(request):

        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "error": None,
            },
        )

    current_key = get_current_key()

    # Permanent public URL
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

    # Secure password comparison
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

    # Create authenticated session
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

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if not is_admin(request):

        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    # --------------------------------------------------------
    # Validate filename
    # --------------------------------------------------------

    if not video.filename:

        raise HTTPException(
            status_code=400,
            detail="Choose a video.",
        )

    # --------------------------------------------------------
    # Validate extension
    # --------------------------------------------------------

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
            detail=(
                "Use MP4, WebM, MOV or M4V."
            ),
        )

    # --------------------------------------------------------
    # Get current video
    # --------------------------------------------------------

    old_key = get_current_key()

    # --------------------------------------------------------
    # Generate random R2 object name
    # --------------------------------------------------------

    new_key = (
        f"videos/"
        f"{secrets.token_hex(16)}"
        f"{extension}"
    )

    # --------------------------------------------------------
    # Upload new video to R2
    # --------------------------------------------------------

    upload_to_r2(
        video,
        new_key,
    )

    # --------------------------------------------------------
    # Save new video state in Supabase
    # --------------------------------------------------------

    try:

        set_current_key(
            new_key
        )

    except Exception:

        # Supabase failed.
        # Remove the newly uploaded video
        # to avoid leaving an unused file in R2.

        delete_r2_video(
            new_key
        )

        raise

    # --------------------------------------------------------
    # Delete previous video
    # --------------------------------------------------------

    if old_key and old_key != new_key:

        delete_r2_video(
            old_key
        )

    # --------------------------------------------------------
    # Return to admin page
    # --------------------------------------------------------

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# ADMIN DELETE CURRENT VIDEO
# ============================================================

@app.post("/admin/delete")
async def delete_video(
    request: Request,
):

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if not is_admin(request):

        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    # --------------------------------------------------------
    # Get current video
    # --------------------------------------------------------

    current_key = get_current_key()

    if current_key:

        # ----------------------------------------------------
        # Remove current state from Supabase
        # ----------------------------------------------------

        clear_current_key()

        # ----------------------------------------------------
        # Remove actual video from R2
        # ----------------------------------------------------

        delete_r2_video(
            current_key
        )

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# ADMIN LOGOUT
# ============================================================

@app.post("/admin/logout")
async def logout(
    request: Request,
):

    request.session.clear()

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# BARCODE IMAGE
# ============================================================

@app.get("/admin/barcode")
async def barcode_image(
    request: Request,
):

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if not is_admin(request):

        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    # --------------------------------------------------------
    # Public URL required
    # --------------------------------------------------------

    if not PUBLIC_URL:

        raise HTTPException(
            status_code=500,
            detail="Set PUBLIC_URL first.",
        )

    # --------------------------------------------------------
    # Permanent barcode destination
    # --------------------------------------------------------

    target = (
        f"{PUBLIC_URL}/video"
    )

    # --------------------------------------------------------
    # Generate Code 128 barcode
    # --------------------------------------------------------

    code = barcode.get(
        "code128",
        target,
        writer=ImageWriter(),
    )

    # --------------------------------------------------------
    # Create PNG in memory
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Return barcode image
    # --------------------------------------------------------

    return StreamingResponse(
        buffer,
        media_type="image/png",
    )
