import os
import logging
from typing import Optional

from minio import Minio
from minio.error import S3Error

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[Minio] = None


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
    return _client


def ensure_bucket():
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info(f"Created bucket: {bucket}")


def upload_file(object_name: str, file_path: str, content_type: str = "image/jpeg"):
    client = get_minio_client()
    client.fput_object(settings.MINIO_BUCKET, object_name, file_path, content_type=content_type)


def get_presigned_url(object_name: str, expires=3600) -> Optional[str]:
    client = get_minio_client()
    try:
        return client.presigned_get_object(settings.MINIO_BUCKET, object_name, expires=expires)
    except S3Error as e:
        logger.error(f"Error getting presigned URL for {object_name}: {e}")
        return None


def object_exists(object_name: str) -> bool:
    client = get_minio_client()
    try:
        client.stat_object(settings.MINIO_BUCKET, object_name)
        return True
    except S3Error:
        return False


def seed_media_from_disk(bot_data_dir: str):
    catalog_dir = os.path.join(bot_data_dir, "catalog")
    if not os.path.exists(catalog_dir):
        logger.error(f"Catalog directory not found: {catalog_dir}")
        return

    uploaded = 0
    for category in os.listdir(catalog_dir):
        category_path = os.path.join(catalog_dir, category)
        if not os.path.isdir(category_path):
            continue

        for filename in os.listdir(category_path):
            if filename.endswith((".jpg", ".png", ".jpeg")):
                file_path = os.path.join(category_path, filename)
                object_name = f"photos/{category}/{filename}"

                if object_exists(object_name):
                    continue

                content_type = "image/png" if filename.endswith(".png") else "image/jpeg"
                try:
                    upload_file(object_name, file_path, content_type)
                    uploaded += 1
                except Exception as e:
                    logger.error(f"Error uploading {file_path}: {e}")

    logger.info(f"Seeded {uploaded} media files")
