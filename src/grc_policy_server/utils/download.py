from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from grc_policy_server.core.config import settings


@dataclass(frozen=True)
class DownloadedFile:
    filename: str
    content: bytes
    url: str


def _guess_filename(url: str) -> str:
    path = urlparse(url).path
    name = path.split("/")[-1] or "downloaded_document"
    return name


async def download_url(url: str) -> DownloadedFile:
    timeout = httpx.Timeout(settings.download_timeout_seconds)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)

    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, follow_redirects=True
    ) as client:
        r = await client.get(url)
        r.raise_for_status()

        content = r.content
        max_bytes = settings.max_download_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError(
                f"Download too large: {len(content)} bytes > {max_bytes} bytes"
            )

        filename = _guess_filename(url)
        return DownloadedFile(filename=filename, content=content, url=url)
