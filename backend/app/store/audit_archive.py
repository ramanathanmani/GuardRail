"""Completed call → S3 (or ./audit/ with AUDIT_ARCHIVE=local) (§5.7). Runs in the background;
failures are logged, never raised."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("guardrail.audit")


class AuditArchive(Protocol):
    async def put(self, key: str, record: dict) -> None: ...


class LocalArchive:
    def __init__(self, root: Path = Path("audit")) -> None:
        self._root = root

    async def put(self, key: str, record: dict) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, json.dumps(record, default=str, indent=2))


class S3Archive:
    def __init__(self, settings: Settings) -> None:
        import boto3

        self._bucket = settings.S3_AUDIT_BUCKET
        self._client = boto3.client("s3", region_name=settings.AWS_REGION)

    async def put(self, key: str, record: dict) -> None:
        body = json.dumps(record, default=str).encode("utf-8")
        await asyncio.to_thread(
            self._client.put_object, Bucket=self._bucket, Key=key, Body=body, ContentType="application/json",
        )


def get_audit_archive(settings: Settings) -> AuditArchive:
    settings.require_audit_archive()
    if settings.AUDIT_ARCHIVE == "s3":
        return S3Archive(settings)
    if settings.AUDIT_ARCHIVE == "local":
        return LocalArchive()
    raise ValueError(f"Unsupported AUDIT_ARCHIVE: {settings.AUDIT_ARCHIVE}")


async def archive_safely(archive: AuditArchive, key: str, record: dict, call_id: str) -> bool:
    try:
        await archive.put(key, record)
        logger.info("call %s: archived to %s", call_id, key)
        return True
    except Exception as exc:  # noqa: BLE001 — archiving never breaks a call
        logger.error("call %s: audit archive failed: %s", call_id, type(exc).__name__)
        return False
