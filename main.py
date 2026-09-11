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
    FileResponse,
)

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

import barcode
from barcode.writer import ImageWriter

from supabase import create_client, Client


# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BASE_DIR / "static"

IMAGES_DIR = STATIC_DIR / "images"

TEMPLATES_DIR = BASE_DIR / "templates"


print("============================================================")
print("APPLICATION PATHS")
print("============================================================")
print("BASE_DIR      :", BASE_DIR)
print("STATIC_DIR    :", STATIC_DIR)
print("IMAGES_DIR    :", IMAGES_DIR)
print("TEMPLATES_DIR :", TEMPLATES_DIR)
print("STATIC EXISTS :", STATIC_DIR.exists())
print("IMAGES EXISTS :", IMAGES_DIR.exists())
print("============================================================")


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

APP_SECRET = os.environ["APP_SECRET"]

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

PUBLIC_URL = os.environ.get(
    "PUBLIC_URL",
    ""
).rstrip("/")


# ============================================================
# SUPABASE
# ============================================================

SUPABASE_URL = os.environ["SUPABASE_URL"]

SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

SUPABASE_STORAGE_BUCKET = os.environ.get(
    "SUPABASE_STORAGE_BUCKET",
    "video",
)


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Permanent Barcode Video"
)


# ============================================================
# STATIC FILES
# ============================================================

# Main static directory:
#
# /static/style.css
# /static/images/1.jpeg
# /static/images/2.jpeg
# etc.
#
# IMPORTANT:
# This must execute before requests are handled.

if not STATIC_DIR.exists():

    print(
        f"WARNING: Static directory does not exist: {STATIC_DIR}"
    )

else:

    print(
        f"Static directory found: {STATIC_DIR}"
    )

    app.mount(
        "/static",
        StaticFiles(
            directory=str(STATIC_DIR)
        ),
        name="static",
    )


# ============================================================
# FALLBACK IMAGE ROUTE
# ============================================================

@app.get(
    "/memory-image/{filename}"
)
async def memory_image(
    filename: str,
):

    # Prevent path traversal
    safe_name = Path(filename).name

    image_path = IMAGES_DIR / safe_name

    if not image_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Image not found: {safe_name}",
        )

    return FileResponse(
        image_path
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
    directory=str(TEMPLATES_DIR)
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
# MEMORY IMAGES
# ============================================================

MEMORY_IMAGES = [
    "/static/images/1.jpeg",
    "/static/images/2.jpeg",
    "/static/images/3.jpeg",
    "/static/images/4.jpeg",
    "/static/images/5.jpeg",
    "/static/images/6.jpeg",
    "/static/images/7.jpeg",
    "/static/images/8.jpeg",
    "/static/images/9.jpeg",
    "/static/images/10.jpeg",
]


# ============================================================
# SUPABASE DATABASE
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
            "Supabase get_current_key error:",
            exc,
        )

        return None


def set_current_key(
    key: str,
):

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
            "Supabase set_current_key error:",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to save video state.",
        )


def clear_current_key():

    """
    Remove the current video state
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
            "Supabase clear_current_key error:",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to clear video state.",
        )


# ============================================================
# SUPABASE STORAGE
# ============================================================

def upload_to_storage(
    video: UploadFile,
    key: str,
):

    """
    Upload video to Supabase Storage.
    """

    try:

        file_data = video.file.read()

        if not file_data:

            raise HTTPException(
                status_code=400,
                detail="Uploaded video is empty.",
            )

        (
            supabase
            .storage
            .from_(SUPABASE_STORAGE_BUCKET)
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
        )

    except HTTPException:
        raise

    except Exception as exc:

        print(
            "Supabase Storage upload error:",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to upload video.",
        )


def delete_storage_video(
    key: str,
):

    """
    Delete a video from Supabase Storage.
    """

    if not key:
        return

    try:

        (
            supabase
            .storage
            .from_(SUPABASE_STORAGE_BUCKET)
            .remove(
                [key]
            )
        )

    except Exception as exc:

        print(
            "Supabase Storage delete error:",
            exc,
        )


def generate_video_url(
    key: str,
):

    """
    Generate a temporary signed URL
    for the private Supabase Storage bucket.
    """

    try:

        response = (
            supabase
            .storage
            .from_(SUPABASE_STORAGE_BUCKET)
            .create_signed_url(
                key,
                3600,
            )
        )

        video_url = None

        if isinstance(response, dict):

            video_url = (
                response.get("signedURL")
                or response.get("signedUrl")
                or response.get("signed_url")
            )

        else:

            try:

                video_url = (
                    response.get("signedURL")
                    or response.get("signedUrl")
                    or response.get("signed_url")
                )

            except Exception:
                pass

        if not video_url:

            raise ValueError(
                f"No signed URL returned: {response}"
            )

        return video_url

    except Exception as exc:

        print(
            "Supabase signed URL error:",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to generate video URL.",
        )


# ============================================================
# ADMIN AUTHENTICATION
# ============================================================

def is_admin(
    request: Request,
):

    return (
        request.session.get("admin")
        is True
    )


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

    # --------------------------------------------------------
    # No video uploaded yet
    # --------------------------------------------------------

    if not current_key:

        return templates.TemplateResponse(
            request=request,
            name="video.html",
            context={
                "request": request,
                "video_url": None,
                "message": (
                    "No video is available right now."
                ),
                "memory_images": MEMORY_IMAGES,
            },
        )

    # --------------------------------------------------------
    # Generate signed URL
    # --------------------------------------------------------

    video_url = generate_video_url(
        current_key
    )

    # --------------------------------------------------------
    # Display video
    # --------------------------------------------------------

    return templates.TemplateResponse(
        request=request,
        name="video.html",
        context={
            "request": request,
            "video_url": video_url,
            "message": None,
            "memory_images": MEMORY_IMAGES,
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

    # --------------------------------------------------------
    # Login required
    # --------------------------------------------------------

    if not is_admin(request):

        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "request": request,
                "error": None,
            },
        )

    # --------------------------------------------------------
    # Current video
    # --------------------------------------------------------

    current_key = get_current_key()

    # --------------------------------------------------------
    # Permanent public video URL
    # --------------------------------------------------------

    public_video_url = (
        f"{PUBLIC_URL}/video"
        if PUBLIC_URL
        else "/video"
    )

    # --------------------------------------------------------
    # Admin page
    # --------------------------------------------------------

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "request": request,
            "current": current_key,
            "public_url": public_video_url,
            "error": None,
        },
    )


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.post(
    "/admin/login"
)
async def admin_login(
    request: Request,
    password: str = Form(...),
):

    # --------------------------------------------------------
    # Check password
    # --------------------------------------------------------

    if not secrets.compare_digest(
        password,
        ADMIN_PASSWORD,
    ):

        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "request": request,
                "error": "Incorrect password.",
            },
            status_code=401,
        )

    # --------------------------------------------------------
    # Login successful
    # --------------------------------------------------------

    request.session["admin"] = True

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# ADMIN VIDEO UPLOAD
# ============================================================

@app.post(
    "/admin/upload"
)
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
    # Check filename
    # --------------------------------------------------------

    if not video.filename:

        raise HTTPException(
            status_code=400,
            detail="Choose a video.",
        )

    # --------------------------------------------------------
    # Allowed extensions
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
    # Get old video
    # --------------------------------------------------------

    old_key = get_current_key()

    # --------------------------------------------------------
    # Generate unique filename
    # --------------------------------------------------------

    new_key = (
        "videos/"
        + secrets.token_hex(16)
        + extension
    )

    # --------------------------------------------------------
    # Upload new video
    # --------------------------------------------------------

    upload_to_storage(
        video,
        new_key,
    )

    # --------------------------------------------------------
    # Save new current video
    # --------------------------------------------------------

    try:

        set_current_key(
            new_key
        )

    except Exception:

        # Database update failed.
        # Delete newly uploaded video.

        delete_storage_video(
            new_key
        )

        raise

    # --------------------------------------------------------
    # Delete previous video
    # --------------------------------------------------------

    if old_key and old_key != new_key:

        delete_storage_video(
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
# DELETE CURRENT VIDEO
# ============================================================

@app.post(
    "/admin/delete"
)
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

    # --------------------------------------------------------
    # Delete video
    # --------------------------------------------------------

    if current_key:

        # First remove database state

        clear_current_key()

        # Then remove storage file

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

@app.post(
    "/admin/logout"
)
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

@app.get(
    "/admin/barcode"
)
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
    # PUBLIC_URL required
    # --------------------------------------------------------

    if not PUBLIC_URL:

        raise HTTPException(
            status_code=500,
            detail="Set PUBLIC_URL first.",
        )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # Barcode ALWAYS points to:
    #
    # /video
    #
    # It does NOT point directly to Supabase.
    #
    # Therefore changing the video does NOT
    # require generating a new barcode.
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
    # Create image in memory
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


# ============================================================
# DEBUG ROUTE - CHECK STATIC FILES
# ============================================================

@app.get(
    "/debug/static"
)
async def debug_static():

    images = []

    if IMAGES_DIR.exists():

        for file in sorted(
            IMAGES_DIR.iterdir()
        ):

            if file.is_file():

                images.append(
                    file.name
                )

    return {
        "base_dir": str(BASE_DIR),
        "static_dir": str(STATIC_DIR),
        "images_dir": str(IMAGES_DIR),
        "static_exists": STATIC_DIR.exists(),
        "images_exists": IMAGES_DIR.exists(),
        "images": images,
    }
