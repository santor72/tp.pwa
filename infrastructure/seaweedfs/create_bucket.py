"""Create the configured SeaweedFS bucket once, safely across restarts."""

import os

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} must be set")
    return value


endpoint = required("S3_ENDPOINT_URL").rstrip("/")
bucket = required("S3_BUCKET")
access_key = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
if bool(access_key) != bool(secret_key):
    raise SystemExit("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together")
client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id=access_key or None,
    aws_secret_access_key=secret_key or None,
    region_name=os.environ.get("S3_REGION", "us-east-1"),
    config=Config(signature_version="s3v4" if access_key else UNSIGNED, connect_timeout=10, read_timeout=10,
                  retries={"max_attempts": 3}, s3={"addressing_style": "path"}),
)

try:
    client.head_bucket(Bucket=bucket)
    print(f"S3 bucket {bucket!r} already exists")
except ClientError as exc:
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if status not in {404, 400}:
        raise
    client.create_bucket(Bucket=bucket)
    client.head_bucket(Bucket=bucket)
    print(f"S3 bucket {bucket!r} created")
