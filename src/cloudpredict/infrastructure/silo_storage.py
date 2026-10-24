"""Silo adapter: an S3-compatible store behind StoragePort and StorageExecutionPort.

Local-demo credentials default to minioadmin; override with
CLOUDPREDICT_SILO_ACCESS_KEY / CLOUDPREDICT_SILO_SECRET_KEY. No secrets are
committed to the repository.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from cloudpredict.domain.exceptions import StorageError
from cloudpredict.domain.models import ObjectHead, StorageObject, Tier

DEFAULT_ENDPOINT = "http://localhost:9000"
DEFAULT_ACCESS_KEY = "minioadmin"
DEFAULT_SECRET_KEY = "minioadmin"
BUCKET_TIERS: Mapping[str, Tier] = {"hot": Tier.HOT, "cold": Tier.COLD}


@dataclass(frozen=True, slots=True)
class BucketStats:
    bucket: str
    objects: int
    size_bytes: int

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1024**3


class SiloStorage:
    """One boto3 client, path-style addressing, synchronous calls only."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        bucket_tiers: Mapping[str, Tier] | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url or os.environ.get(
            "CLOUDPREDICT_SILO_ENDPOINT", DEFAULT_ENDPOINT
        )
        self._bucket_tiers = dict(bucket_tiers or BUCKET_TIERS)
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=access_key
            or os.environ.get("CLOUDPREDICT_SILO_ACCESS_KEY", DEFAULT_ACCESS_KEY),
            aws_secret_access_key=secret_key
            or os.environ.get("CLOUDPREDICT_SILO_SECRET_KEY", DEFAULT_SECRET_KEY),
            region_name=region,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    # -- setup -----------------------------------------------------------

    def ensure_bucket(self, bucket: str) -> bool:
        """Create the bucket if absent; returns True when it was created."""
        try:
            self._client.head_bucket(Bucket=bucket)
            return False
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise StorageError(f"cannot head bucket {bucket}: {error}") from error
        try:
            self._client.create_bucket(Bucket=bucket)
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot create bucket {bucket}: {error}") from error
        return True

    def set_public_read(self, bucket: str) -> None:
        """Demo-only: anonymous GET on every Object in the bucket."""
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
        try:
            self._client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot set policy on {bucket}: {error}") from error

    # -- inventory -------------------------------------------------------

    def list_objects(self, bucket: str) -> list[StorageObject]:
        tier = self._bucket_tiers.get(bucket, Tier.HOT)
        objects: list[StorageObject] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    objects.append(
                        StorageObject(
                            bucket=bucket,
                            key=str(item["Key"]),
                            size_bytes=int(item["Size"]),
                            created_at=item["LastModified"],
                            tier=tier,
                        )
                    )
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot list {bucket}: {error}") from error
        return objects

    def bucket_stats(self, bucket: str) -> BucketStats:
        count = 0
        total = 0
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    count += 1
                    total += int(item["Size"])
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot size {bucket}: {error}") from error
        return BucketStats(bucket=bucket, objects=count, size_bytes=total)

    # -- execution -------------------------------------------------------

    def head(self, bucket: str, key: str) -> ObjectHead | None:
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageError(f"cannot head {bucket}/{key}: {error}") from error
        return ObjectHead(
            bucket=bucket,
            key=key,
            size_bytes=int(response["ContentLength"]),
            etag=str(response["ETag"]).strip('"'),
        )

    def upload_file(
        self, bucket: str, key: str, path: Path, content_type: str | None = None
    ) -> str:
        """Single-part upload so the ETag stays a plain MD5 (needed for copy verification)."""
        extra = {"ContentType": content_type} if content_type else {}
        try:
            with path.open("rb") as handle:
                response = self._client.put_object(Bucket=bucket, Key=key, Body=handle, **extra)
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot upload {bucket}/{key}: {error}") from error
        return str(response["ETag"]).strip('"')

    def put_bytes(self, bucket: str, key: str, payload: bytes) -> str:
        try:
            response = self._client.put_object(Bucket=bucket, Key=key, Body=payload)
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot put {bucket}/{key}: {error}") from error
        return str(response["ETag"]).strip('"')

    def copy_object(self, source_bucket: str, key: str, destination_bucket: str) -> str:
        try:
            self._client.copy_object(
                Bucket=destination_bucket,
                Key=key,
                CopySource={"Bucket": source_bucket, "Key": key},
            )
        except (ClientError, BotoCoreError) as error:
            raise StorageError(
                f"cannot copy {source_bucket}/{key} -> {destination_bucket}: {error}"
            ) from error
        head = self.head(destination_bucket, key)
        if head is None:
            raise StorageError(f"copy reported success but {destination_bucket}/{key} is absent")
        return head.etag

    def delete_object(self, bucket: str, key: str) -> None:
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as error:
            raise StorageError(f"cannot delete {bucket}/{key}: {error}") from error
