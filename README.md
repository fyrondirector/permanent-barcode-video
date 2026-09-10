# Permanent 1D Barcode Video

This project uses a **Code 128 1D barcode**, NOT a QR code.

The barcode points to one permanent URL:
`https://YOUR-RENDER-SERVICE.onrender.com/video`

The video behind that URL can be replaced from `/admin`.

## Phone-only deployment

Use GitHub + Render + Cloudflare R2.

### Environment variables in Render

APP_SECRET = a long random secret
ADMIN_PASSWORD = your private admin password
R2_ENDPOINT = https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID = R2 S3 API access key
R2_SECRET_ACCESS_KEY = R2 S3 API secret
R2_BUCKET = your bucket name
PUBLIC_URL = https://YOUR-RENDER-SERVICE.onrender.com

### Workflow

1. Deploy the app.
2. Open `/admin`.
3. Log in.
4. The page generates the same Code 128 barcode for `/video`.
5. Save/print that barcode.
6. Upload Video A.
7. Later, upload Video B; Video A is removed and the same barcode now leads to Video B.

The barcode contains the permanent URL, not the video file.
