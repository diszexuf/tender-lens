"""Клиент S3-совместимого хранилища (Garage)."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any

import aioboto3
from botocore.config import Config

from app.core.config import Settings

# Garage — не AWS: path-style адресация и без обязательных CRC-контрольных сумм,
# которые новые версии botocore добавляют по умолчанию.
_BOTO_CONFIG = Config(
    s3={"addressing_style": "path"},
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=30,
)


def s3_client(settings: Settings) -> AbstractAsyncContextManager[Any]:
    """Асинхронный клиент S3: `async with s3_client(settings) as s3: ...`."""
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=_BOTO_CONFIG,
    )
