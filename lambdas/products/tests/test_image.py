import base64

import boto3
import pytest

from tests.conftest import IMAGES_BUCKET, body_of
from handlers.create import handle_create_product
from handlers.image import handle_upload_image_for_sku, handle_upload_product_image

ONE_PX_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d763f8ffff3f0005fe02fea739b1b50000000049"
        "454e44ae426082"
    )
).decode()


def _create(db, make_event, **overrides):
    body = {"sku": "IMG-1", "name": "Product"}
    body.update(overrides)
    resp = handle_create_product(db, make_event("POST", body=body))
    assert resp["statusCode"] == 200 or resp["statusCode"] == 201, resp
    return body_of(resp)["data"]


class TestUploadProductImage:
    def test_uploads_and_persists_sku_derived_key(self, db, images_bucket, make_event):
        created = _create(db, make_event, sku="Dry-CK-001")
        resp = handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 200, resp
        data = body_of(resp)["data"]
        assert data["image_url"] == "img/dry-ck-001.jpg"

        obj = boto3.client("s3", region_name="us-east-1").get_object(Bucket=IMAGES_BUCKET, Key="img/dry-ck-001.jpg")
        assert obj["ContentType"] == "image/png"
        assert obj["Body"].read() == base64.b64decode(ONE_PX_PNG)

    def test_reupload_overwrites_same_key(self, db, images_bucket, make_event):
        created = _create(db, make_event, sku="IMG-2")
        handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "image/png", "data": ONE_PX_PNG})
        )
        resp = handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "image/jpeg", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["image_url"] == "img/img-2.jpg"

    def test_rejects_missing_product(self, db, images_bucket, make_event):
        resp = handle_upload_product_image(
            db, "does-not-exist", make_event("POST", body={"content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 404

    def test_rejects_blank_product_id(self, db, images_bucket, make_event):
        resp = handle_upload_product_image(
            db, "", make_event("POST", body={"content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 400

    def test_rejects_unsupported_content_type(self, db, images_bucket, make_event):
        created = _create(db, make_event, sku="IMG-3")
        resp = handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "application/pdf", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 400

    def test_rejects_missing_data(self, db, images_bucket, make_event):
        created = _create(db, make_event, sku="IMG-4")
        resp = handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "image/png"})
        )
        assert resp["statusCode"] == 400

    def test_rejects_invalid_base64(self, db, images_bucket, make_event):
        created = _create(db, make_event, sku="IMG-5")
        resp = handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "image/png", "data": "not-base64!!"})
        )
        assert resp["statusCode"] == 400

    def test_missing_bucket_env_var_returns_500(self, db, images_bucket, monkeypatch, make_event):
        created = _create(db, make_event, sku="IMG-6")
        monkeypatch.delenv("PRODUCT_IMAGES_BUCKET", raising=False)
        resp = handle_upload_product_image(
            db, created["id"], make_event("POST", body={"content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 500


class TestUploadImageForSku:
    def test_uploads_without_a_product_and_returns_key(self, db, images_bucket, make_event):
        resp = handle_upload_image_for_sku(
            db, make_event("POST", body={"sku": "New-SKU-1", "content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 200, resp
        assert body_of(resp)["data"]["image_url"] == "img/new-sku-1.jpg"

        obj = boto3.client("s3", region_name="us-east-1").get_object(Bucket=IMAGES_BUCKET, Key="img/new-sku-1.jpg")
        assert obj["Body"].read() == base64.b64decode(ONE_PX_PNG)

    def test_rejects_missing_sku(self, db, images_bucket, make_event):
        resp = handle_upload_image_for_sku(
            db, make_event("POST", body={"content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 400

    def test_rejects_blank_sku(self, db, images_bucket, make_event):
        resp = handle_upload_image_for_sku(
            db, make_event("POST", body={"sku": "   ", "content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 400

    def test_rejects_sku_already_in_use(self, db, images_bucket, make_event):
        created = _create(db, make_event, sku="TAKEN-1")
        resp = handle_upload_image_for_sku(
            db, make_event("POST", body={"sku": "TAKEN-1", "content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 409

        # And the existing product's image must be untouched by the rejected attempt.
        with pytest.raises(Exception):
            boto3.client("s3", region_name="us-east-1").get_object(Bucket=IMAGES_BUCKET, Key="img/taken-1.jpg")

    def test_rejects_unsupported_content_type(self, db, images_bucket, make_event):
        resp = handle_upload_image_for_sku(
            db, make_event("POST", body={"sku": "New-SKU-2", "content_type": "application/pdf", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 400

    def test_missing_bucket_env_var_returns_500(self, db, images_bucket, monkeypatch, make_event):
        monkeypatch.delenv("PRODUCT_IMAGES_BUCKET", raising=False)
        resp = handle_upload_image_for_sku(
            db, make_event("POST", body={"sku": "New-SKU-3", "content_type": "image/png", "data": ONE_PX_PNG})
        )
        assert resp["statusCode"] == 500


class TestUploadProductImageRouting:
    def test_post_with_image_resource_routes_correctly(self, images_bucket, make_event):
        import lambda_handler

        created = body_of(
            lambda_handler.lambda_handler(make_event("POST", body={"sku": "ROUTE-IMG-1", "name": "X"}), None)
        )["data"]

        resp = lambda_handler.lambda_handler(
            make_event(
                "POST",
                product_id=created["id"],
                body={"content_type": "image/jpeg", "data": ONE_PX_PNG},
                resource="/products/{productid}/image",
            ),
            None,
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["image_url"] == "img/route-img-1.jpg"

    def test_plain_post_still_routes_to_create(self, images_bucket, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(
            make_event("POST", body={"sku": "ROUTE-IMG-2", "name": "X"}), None
        )
        assert resp["statusCode"] == 201

    def test_post_products_image_with_no_product_id_routes_to_sku_upload(self, images_bucket, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(
            make_event(
                "POST",
                body={"sku": "ROUTE-IMG-3", "content_type": "image/png", "data": ONE_PX_PNG},
                resource="/products/image",
            ),
            None,
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["image_url"] == "img/route-img-3.jpg"
