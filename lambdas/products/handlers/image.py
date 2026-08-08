"""
POST /products/{productid}/image — admin panel product image upload for an
                                    existing product.
POST /products/image             — same upload, for the "new product" form,
                                    before the product has been created yet
                                    (keyed directly by the sku the caller
                                    intends to use, not a product_id).

Uploads happen through this Lambda rather than a presigned browser->S3 PUT:
the shared images bucket (chonky-images-<env>, see chonky-cat-fe's
scripts/push-images.mjs) only has a bucket policy for public GetObject on
img/* — no CORS rules for PUT — since it was only ever set up to be read
via <img src>, not written to from a browser. Routing the upload through
API Gateway (which already has CORS wired up via shared/cors.py) sidesteps
that entirely, at the cost of the file passing through as a base64 JSON
body instead of a direct-to-S3 stream.

Key naming intentionally matches the convention already used by real
product records and by chonky-cat-fe's init-images.mjs: img/<sku, lowercased>.jpg
— always a literal ".jpg" suffix regardless of the uploaded file's actual
type, so re-uploads deterministically overwrite the same object and every
product's image lives at a predictable, sku-derived key. The Content-Type
we set on the object (from the caller's declared content_type) is what
actually governs how browsers render it — the ".jpg" extension is just
the established naming scheme, not a format guarantee.
"""

import base64
import os
import traceback

import boto3
from botocore.exceptions import ClientError

from common import err, now_iso, ok, parse_body, serialize_product

# Keeps uploads to real image types and rules out someone using this as an
# arbitrary-file-upload endpoint via a spoofed content_type.
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB — comfortably under API Gateway's 10MB payload cap once base64 overhead is added


def _image_key_for_sku(sku: str) -> str:
    return f"img/{sku.strip().lower()}.jpg"


def _parse_upload_body(event: dict) -> tuple[bytes, str] | dict:
    """Returns (image_bytes, content_type) on success, or an err() response dict on failure."""
    try:
        body = parse_body(event)
    except Exception:
        return err("Request body must be valid JSON", status=400)

    content_type = body.get("content_type")
    if content_type not in ALLOWED_CONTENT_TYPES:
        return err(
            f"'content_type' must be one of: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
            status=400,
        )

    data = body.get("data")
    if not data or not isinstance(data, str):
        return err("'data' (base64-encoded image bytes) is required", status=400)

    try:
        image_bytes = base64.b64decode(data, validate=True)
    except Exception:
        return err("'data' must be valid base64", status=400)

    if len(image_bytes) > MAX_IMAGE_BYTES:
        return err(f"Image exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)}MB limit", status=400)

    return image_bytes, content_type


def _put_image(bucket: str, sku: str, image_bytes: bytes, content_type: str) -> str | dict:
    """Returns the S3 key on success, or an err() response dict on failure."""
    key = _image_key_for_sku(sku)
    # ExpectedBucketOwner guards against a confused-deputy attack: without
    # it, if this bucket name were ever reused by another AWS account (e.g.
    # after deletion), put_object would silently start writing images into
    # that account's bucket instead of failing.
    put_kwargs = {"Bucket": bucket, "Key": key, "Body": image_bytes, "ContentType": content_type}
    bucket_owner = os.environ.get("PRODUCT_IMAGES_BUCKET_OWNER")
    if bucket_owner:
        put_kwargs["ExpectedBucketOwner"] = bucket_owner
    try:
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
        s3.put_object(**put_kwargs)
    except ClientError as e:
        print(f"[ERROR] Failed to upload product image to S3: {e}")
        print(traceback.format_exc())
        return err("Failed to upload image", status=500)
    return key


def handle_upload_product_image(db, product_id: str, event: dict) -> dict:
    if not product_id:
        return err("Invalid product ID format", status=400)

    bucket = os.environ.get("PRODUCT_IMAGES_BUCKET")
    if not bucket:
        return err("Image uploads are not configured", status=500)

    try:
        existing = db.get_product(product_id)
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database query failed: {str(e)}", status=500)

    if not existing:
        return err(f"Product with ID {product_id} not found", status=404)

    sku = existing.get("sku")
    if not sku:
        return err("Product has no SKU to derive an image filename from", status=500)

    parsed = _parse_upload_body(event)
    if isinstance(parsed, dict):
        return parsed
    image_bytes, content_type = parsed

    key = _put_image(bucket, sku, image_bytes, content_type)
    if isinstance(key, dict):
        return key

    try:
        updated = db.update_product(product_id, {"image_url": key, "updated_at": now_iso()}, [])
        return ok({"data": serialize_product(updated)})
    except Exception as e:
        print(f"[ERROR] Unexpected DynamoDB error: {e}")
        print(traceback.format_exc())
        return err(f"Database write failed: {str(e)}", status=500)


def handle_upload_image_for_sku(db, event: dict) -> dict:
    """Pre-create upload for the "new product" form — no product row exists
    yet, so this only writes to S3 and hands back the key; the caller is
    responsible for including it as image_url on the subsequent
    POST /products. Guards against silently overwriting an existing
    product's photo if the typed SKU happens to collide with one already
    in use (the create endpoint would reject that SKU anyway, but only
    after this upload would have already clobbered the other product's
    image)."""
    bucket = os.environ.get("PRODUCT_IMAGES_BUCKET")
    if not bucket:
        return err("Image uploads are not configured", status=500)

    try:
        body = parse_body(event)
    except Exception:
        return err("Request body must be valid JSON", status=400)

    sku = body.get("sku")
    if not sku or not isinstance(sku, str) or not sku.strip():
        return err("'sku' is required", status=400)
    sku = sku.strip()

    try:
        clash = db.get_product_by_sku(sku)
    except Exception as e:
        print(f"[ERROR] SKU uniqueness check failed: {e}")
        print(traceback.format_exc())
        return err("Database query failed", status=500)
    if clash:
        return err(f"A product with sku '{sku}' already exists", status=409)

    parsed = _parse_upload_body(event)
    if isinstance(parsed, dict):
        return parsed
    image_bytes, content_type = parsed

    key = _put_image(bucket, sku, image_bytes, content_type)
    if isinstance(key, dict):
        return key

    return ok({"data": {"image_url": key}})
