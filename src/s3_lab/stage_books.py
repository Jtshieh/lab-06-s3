#!/usr/bin/env python3
"""Download a Gutenberg corpus and publish it in an S3 bucket."""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)
s3 = boto3.client("s3", region_name="us-east-1")


def parse_args() -> tuple[str, str, str]:
    """Return the book list, index page, and destination bucket arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("list_file", help="Text file containing one book URL per line")
    parser.add_argument("index_path", help="HTML page linking to the books")
    parser.add_argument("bucket", help="Public S3 bucket name")
    args = parser.parse_args()
    return args.list_file, args.index_path, args.bucket


def download_books(list_file: str, dest_dir: str) -> list[str]:
    """Cache successful downloads locally, pausing between HTTP requests."""
    paths = []
    try:
        destination = Path(dest_dir)
        destination.mkdir(parents=True, exist_ok=True)
        urls = Path(list_file).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        log.error("Cannot read the book list or create the download directory: %s", error)
        return paths
    for url in urls:
        url = url.strip()
        if not url:
            continue
        filename = Path(urlparse(url).path).name
        if not filename:
            log.error("URL has no file name: %s", url)
            continue
        path = destination / filename
        if path.is_file() and path.stat().st_size:
            log.info("Using cached book: %s", path)
            paths.append(str(path))
            continue
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            if not response.content:
                raise ValueError("Empty book response")
            # Replace only after a complete download so a rerun cannot use a partial file.
            temporary = path.with_suffix(path.suffix + ".part")
            temporary.write_bytes(response.content)
            temporary.replace(path)
            paths.append(str(path))
            log.info("Downloaded %s", path)
        except (requests.RequestException, OSError, ValueError) as error:
            log.error("Error downloading %s: %s", url, error)
        finally:
            time.sleep(1)
    return paths


def create_bucket(bucket: str) -> bool:
    """Reuse an accessible bucket or create one in us-east-1."""
    try:
        s3.head_bucket(Bucket=bucket)
        log.info("Using existing bucket: %s", bucket)
        return True
    except ClientError as error:
        if str(error.response["Error"]["Code"]) not in {"404", "NoSuchBucket", "NotFound"}:
            log.error("Cannot access bucket %s: %s", bucket, error)
            return False
    except BotoCoreError as error:
        log.error("Cannot check bucket %s: %s", bucket, error)
        return False
    try:
        # us-east-1 does not accept a LocationConstraint when creating a bucket.
        s3.create_bucket(Bucket=bucket)
        log.info("Created bucket: %s", bucket)
        return True
    except (ClientError, BotoCoreError) as error:
        log.error("Cannot create bucket %s: %s", bucket, error)
        return False


def make_public(bucket: str) -> bool:
    """Allow anonymous reads of objects in this bucket, without write access."""
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket}/*",
        }],
    }
    try:
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": False,
                "IgnorePublicAcls": False,
                "BlockPublicPolicy": False,
                "RestrictPublicBuckets": False,
            },
        )
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        log.info("Enabled public object reads: %s", bucket)
        return True
    except (ClientError, BotoCoreError) as error:
        log.error("Cannot make bucket %s public: %s", bucket, error)
        return False


def upload_files(bucket: str, file_paths: list[str]) -> int:
    """Upload books at the bucket root and return the successful upload count."""
    uploaded = 0
    for filename in file_paths:
        path = Path(filename)
        try:
            s3.upload_file(
                str(path), bucket, path.name,
                ExtraArgs={"ContentType": "text/plain; charset=utf-8"},
            )
            uploaded += 1
            log.info("Uploaded s3://%s/%s", bucket, path.name)
        except (ClientError, BotoCoreError, OSError, boto3.exceptions.S3UploadFailedError) as error:
            log.error("Cannot upload %s: %s", path, error)
    return uploaded


def upload_index(bucket: str, index_path: str) -> bool:
    """Upload the index as HTML so a browser renders the collection page."""
    try:
        s3.upload_file(index_path, bucket, "index.html", ExtraArgs={"ContentType": "text/html"})
        log.info("Uploaded index.html")
        return True
    except (ClientError, BotoCoreError, OSError, boto3.exceptions.S3UploadFailedError) as error:
        log.error("Cannot upload index %s: %s", index_path, error)
        return False


def main() -> None:
    """Download, publish, and report whether the entire requested corpus uploaded."""
    list_file, index_path, bucket = parse_args()
    try:
        expected = sum(bool(line.strip()) for line in Path(list_file).read_text(encoding="utf-8").splitlines())
    except OSError as error:
        log.error("Cannot read %s: %s", list_file, error)
        sys.exit(1)
    files = download_books(list_file, "books")
    if not files or not create_bucket(bucket) or not make_public(bucket):
        sys.exit(1)
    uploaded = upload_files(bucket, files)
    index_ok = upload_index(bucket, index_path)
    log.info("Public index: https://%s.s3.amazonaws.com/index.html", bucket)
    if uploaded != expected or not index_ok:
        log.error("Incomplete collection: %s/%s books uploaded; index uploaded: %s", uploaded, expected, index_ok)
        sys.exit(1)
    log.info("Published all %s books and index.html.", uploaded)


if __name__ == "__main__":
    main()
