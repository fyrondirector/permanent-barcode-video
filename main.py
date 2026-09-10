import os
import secrets
from pathlib import Path
from io import BytesIO

import boto3
from botocore.client import Config
import barcode
from barcode.writer import ImageWriter
from fastapi import FastAPI, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

APP_SECRET = os.environ["APP_SECRET"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
R2_ENDPOINT = os.environ["R2_ENDPOINT"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

app = FastAPI(title="Permanent Barcode Video")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET, https_only=True, same_site="lax")
templates = Jinja2Templates(directory="templates")

r2 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

STATE_KEY = "current-video.txt"

def get_current_key():
    try:
        obj = r2.get_object(Bucket=R2_BUCKET, Key=STATE_KEY)
        return obj["Body"].read().decode("utf-8").strip() or None
    except Exception:
        return None

def set_current_key(key):
    r2.put_object(Bucket=R2_BUCKET, Key=STATE_KEY, Body=key.encode(), ContentType="text/plain")

def delete_current():
    key = get_current_key()
    if key:
        try:
            r2.delete_object(Bucket=R2_BUCKET, Key=key)
        finally:
            try:
                r2.delete_object(Bucket=R2_BUCKET, Key=STATE_KEY)
            except Exception:
                pass

def is_admin(request: Request):
    return request.session.get("admin") is True

@app.get("/", response_class=HTMLResponse)
async def home():
    return RedirectResponse("/video", status_code=302)

@app.get("/video", response_class=HTMLResponse)
async def video_page(request: Request):
    key = get_current_key()
    if not key:
        return templates.TemplateResponse("video.html", {
            "request": request, "video_url": None,
            "message": "No video is available right now."
        })
    url = r2.generate_presigned_url(
        "get_object", Params={"Bucket": R2_BUCKET, "Key": key}, ExpiresIn=3600
    )
    return templates.TemplateResponse("video.html", {
        "request": request, "video_url": url, "message": None
    })

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not is_admin(request):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "current": get_current_key(),
        "public_url": PUBLIC_URL + "/video" if PUBLIC_URL else "/video",
        "error": None
    })

@app.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        return templates.TemplateResponse(
            "admin_login.html", {"request": request, "error": "Incorrect password."}, status_code=401
        )
    request.session["admin"] = True
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/upload")
async def upload_video(request: Request, video: UploadFile = File(...)):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Login required.")
    if not video.filename:
        raise HTTPException(status_code=400, detail="Choose a video.")

    allowed = {".mp4", ".webm", ".mov", ".m4v"}
    ext = Path(video.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Use MP4, WebM, MOV or M4V.")

    new_key = f"videos/{secrets.token_hex(16)}{ext}"
    r2.upload_fileobj(
        video.file, R2_BUCKET, new_key,
        ExtraArgs={"ContentType": video.content_type or "video/mp4"}
    )

    old_key = get_current_key()
    set_current_key(new_key)

    if old_key and old_key != new_key:
        try:
            r2.delete_object(Bucket=R2_BUCKET, Key=old_key)
        except Exception:
            pass

    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/delete")
async def delete_video(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Login required.")
    delete_current()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin", status_code=303)

@app.get("/admin/barcode")
async def barcode_image(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Login required.")
    if not PUBLIC_URL:
        raise HTTPException(status_code=500, detail="Set PUBLIC_URL first.")

    target = PUBLIC_URL + "/video"
    # Code 128 supports the full URL and is widely supported by barcode scanners.
    code = barcode.get("code128", target, writer=ImageWriter())
    buffer = BytesIO()
    code.write(buffer, options={
        "module_width": 0.25,
        "module_height": 18,
        "font_size": 9,
        "text_distance": 4,
        "quiet_zone": 6,
        "write_text": True,
    })
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")
