"""An S3-compatible bucket (S3, R2, MinIO, ...). `boto3` is the `s3` extra:
`pip install postdesk[s3]`; the Docker image has it."""

from __future__ import annotations

import functools

from postdesk.config import settings

IMMUTABLE = "public, max-age=31536000, immutable"


def key(sha256: str) -> str:
    return f"assets/{sha256[:2]}/{sha256}"


@functools.cache
def _client(endpoint: str, key_id: str, secret: str):
    try:
        import boto3
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("S3 storage needs boto3: pip install 'postdesk[s3]'") from e
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key_id, aws_secret_access_key=secret,
                        region_name="auto")


def client():
    return _client(settings.storage_s3_endpoint, settings.storage_s3_access_key_id,
                   settings.storage_s3_secret_access_key)


def has(sha256: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client().head_object(Bucket=settings.storage_s3_bucket, Key=key(sha256))
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def put(sha256: str, data: bytes, content_type: str) -> None:
    client().put_object(Bucket=settings.storage_s3_bucket, Key=key(sha256), Body=data, ContentType=content_type,
                        CacheControl=IMMUTABLE)


def get(sha256: str) -> bytes:
    return client().get_object(Bucket=settings.storage_s3_bucket, Key=key(sha256))["Body"].read()
