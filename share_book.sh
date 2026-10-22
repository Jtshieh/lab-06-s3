#!/bin/bash
# Download one Gutenberg book, keep it private in S3, and print a temporary URL.
set -euo pipefail

log() {
    printf '%s\n' "$*" >&2
}

main() {
    if [[ "$#" -ne 3 ]]; then
        log "Usage: $0 GUTENBERG_TEXT_URL PRIVATE_BUCKET EXPIRATION_SECONDS"
        exit 1
    fi
    local url="$1" bucket="$2" expiration="$3" filename
    if [[ ! "$url" =~ ^https://www\.gutenberg\.org/cache/epub/[0-9]+/pg[0-9]+\.txt$ ]]; then
        log "Use a Project Gutenberg Plain Text UTF-8 URL ending in pgNUMBER.txt."
        exit 1
    fi
    if [[ ! "$bucket" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
        log "Invalid bucket name."
        exit 1
    fi
    if [[ ! "$expiration" =~ ^[1-9][0-9]{0,5}$ ]] || (( 10#$expiration > 604800 )); then
        log "Expiration must be an integer from 1 through 604800 seconds."
        exit 1
    fi
    filename="${url##*/}"

    # Save a completed download, avoiding a partial book if curl fails.
    mkdir -p books
    trap 'rm -f -- books/.share-book-download.part' EXIT
    log "Downloading $filename"
    curl -fL --connect-timeout 15 --max-time 120 "$url" -o books/.share-book-download.part
    mv -- books/.share-book-download.part "books/$filename"

    # A new bucket is private by default. Do not attach a public policy or ACL.
    if ! aws s3 ls "s3://$bucket" > /dev/null 2>&1; then
        log "Creating private bucket: $bucket"
        aws s3 mb "s3://$bucket" --region us-east-1 >&2
    fi
    log "Uploading $filename to $bucket"
    aws s3 cp "books/$filename" "s3://$bucket/$filename" \
        --content-type "text/plain; charset=utf-8" >&2

    # Keep stdout limited to the URL, so it can be captured for the README.
    aws s3 presign "s3://$bucket/$filename" --expires-in "$expiration" --region us-east-1
}

main "$@"
