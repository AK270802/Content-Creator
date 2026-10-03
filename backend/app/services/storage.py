import io
from loguru import logger
from minio import Minio
from minio.error import S3Error

from app.config import settings


class StorageService:
    def __init__(self) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket
        # Separate client for presigned URLs — uses the browser-reachable host so
        # the signature's Host header matches what the browser sends.
        if settings.minio_public_url:
            from urllib.parse import urlparse
            parsed = urlparse(settings.minio_public_url)
            self._presign_client = Minio(
                parsed.netloc,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=parsed.scheme == "https",
            )
            # Pre-seed region so no network call is made when presigning.
            # The Minio SDK caches regions in _region_map to avoid repeated
            # GetBucketLocation requests; without this, it tries to reach
            # localhost:9000 from inside the container and fails.
            self._presign_client._region_map[self._bucket] = "us-east-1"
        else:
            self._presign_client = self._client

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info(f"Created MinIO bucket: {self._bucket}")

    def upload_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        logger.debug(f"Uploaded {key} ({len(data)} bytes)")

    def upload_file(self, key: str, file_path: str, content_type: str = "application/octet-stream") -> None:
        self._client.fput_object(self._bucket, key, file_path, content_type=content_type)
        logger.debug(f"Uploaded file {file_path} → {key}")

    def download_to_file(self, key: str, dest_path: str) -> None:
        self._client.fget_object(self._bucket, key, dest_path)

    def presign_url(self, key: str, expires_seconds: int = 3600) -> str:
        from datetime import timedelta
        return self._presign_client.presigned_get_object(
            self._bucket, key, expires=timedelta(seconds=expires_seconds)
        )

    def delete(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except S3Error as e:
            logger.warning(f"Delete {key} failed: {e}")


storage_service = StorageService()
