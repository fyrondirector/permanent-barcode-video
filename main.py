import os
import secrets
from pathlib import Path
from io import BytesIO

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

import barcode
from barcode.writer import ImageWriter

from supabase import create_client, Client


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

APP_SECRET = os.environ["APP_SECRET"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

# Public URL of the Render application
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

# Supabase Storage bucket
SUPABASE_STORAGE_BUCKET = os.environ.get(
    "SUPABASE_STORAGE_BUCKET",
    "videos",
)


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Permanent Barcode Video"
)

app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    https_only=True,
    same_site="lax",
)

templates = Jinja2Templates(
    directory="templates"
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
# SUPABASE DATABASE FUNCTIONS
# ============================================================

def get_current_key():
    """
    Get the currently active video path
    from the Supabase app_state table.
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

        return None


def set_current_key(key: str):
    """
    Store the currently active video path
    in Supabase.
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
    Remove the currently active video
    from the app_state table.
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
# SUPABASE STORAGE FUNCTIONS
# ============================================================

def upload_to_storage(
    video: UploadFile,
    key: str,
):
    """
    Upload the video to Supabase Storage.
    """

    try:
        file_data = video.file.read()

        if not file_data:
            raise HTTPException(
                status_code=400,
                detail="Uploaded video is empty.",
            )

        supabase.storage \
            .from_(SUPABASE_STORAGE_BUCKET) \
            .upload(
                path=key,
                file=file_data,
                file_options={
                    "content-type": (
                        video.content_type
                        or "video/mp4"
                    ),
                    "cache-control": "3600",
                    "upsert": "false",
                },
            )

    except HTTPException:
        raise

    except Exception as exc:
        print(
            f"Supabase Storage upload error: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to upload video.",
        )


def delete_storage_video(key: str):
    """
    Delete a video from Supabase Storage.
    """

    if not key:
        return

    try:
        (
            supabase.storage
            .from_(SUPABASE_STORAGE_BUCKET)
            .remove([key])
        )

    except Exception as exc:
        print(
            f"Supabase Storage delete error "
            f"for {key}: {exc}"
        )


def generate_video_url(key: str):
    """
    Generate a temporary signed URL for a
    private Supabase Storage bucket.
    """

    try:
        response = (
            supabase.storage
            .from_(SUPABASE_STORAGE_BUCKET)
            .create_signed_url(
                key,
                3600,
            )
        )

        # Different supabase-py versions may use
        # slightly different response key names.
        video_url = (
            response.get("signedURL")
            or response.get("signedUrl")
            or response.get("signed_url")
        )

        if not video_url:
            raise ValueError(
                f"No signed URL returned: {response}"
            )

        return video_url

    except Exception as exc:
        print(
            f"Supabase signed URL error: {exc}"
        )

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
            detail=(
                "Use MP4, WebM, MOV or M4V."
            ),
        )

    # Get currently active video
    old_key = get_current_key()

    # Generate a unique Storage path
    new_key = (
        f"videos/"
        f"{secrets.token_hex(16)}"
        f"{extension}"
    )

    # Upload new video
    upload_to_storage(
        video,
        new_key,
    )

    # Save new video state
    try:

        set_current_key(
            new_key
        )

    except Exception:

        # If database update fails,
        # remove the newly uploaded file.
        delete_storage_video(
            new_key
        )

        raise

    # Delete previous video
    if old_key and old_key != new_key:

        delete_storage_video(
            old_key
        )

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

    if not is_admin(request):

        raise HTTPException(
            status_code=401,
            detail="Login required.",
        )

    current_key = get_current_key()

    if current_key:

        # Remove database state first
        clear_current_key()

        # Remove actual video
        delete_storage_video(
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

    # IMPORTANT:
    # The barcode points to the permanent
    # application URL, NOT the video file.
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
