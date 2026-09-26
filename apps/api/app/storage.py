from typing import Protocol

import boto3
from botocore.client import BaseClient

from app.settings import Settings, get_settings


class ObjectStorage(Protocol):
    def put_bytes(self, key: str, body: bytes, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def check_ready(self) -> None: ...


class S3ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name="us-east-1",
        )

    def _ensure_bucket(self) -> None:
        buckets = self.client.list_buckets().get("Buckets", [])
        if self.bucket not in {bucket["Name"] for bucket in buckets}:
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, key: str, body: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def check_ready(self) -> None:
        self.client.list_buckets()


def get_storage() -> ObjectStorage:
    return S3ObjectStorage(get_settings())
