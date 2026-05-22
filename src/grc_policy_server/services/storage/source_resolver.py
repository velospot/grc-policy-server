from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from grc_policy_server.core.logging import logging
from grc_policy_server.services.storage.storage_provider_store import (
    StorageProviderRecord,
    StorageProviderStore,
)
from grc_policy_server.utils.download import DownloadedFile, download_url

logger = logging.getLogger(__name__)


_GDRIVE_FILE_ID_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


def _gdrive_direct_download_url(url: str) -> str | None:
    parsed = urlparse(url)
    if "drive.google.com" not in (parsed.netloc or ""):
        return None

    qs = parse_qs(parsed.query or "")
    if "id" in qs and qs["id"]:
        file_id = qs["id"][0]
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    match = _GDRIVE_FILE_ID_RE.search(parsed.path or "")
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    return None


def _parse_s3_uri(uri: str) -> tuple[str, str] | None:
    if not uri.lower().startswith("s3://"):
        return None
    parsed = urlparse(uri)
    bucket = (parsed.netloc or "").strip()
    key = (parsed.path or "").lstrip("/")
    if not bucket or not key:
        return None
    return bucket, key


def _parse_azblob_uri(uri: str) -> tuple[str, str] | None:
    scheme = uri.split("://", 1)[0].lower() if "://" in uri else ""
    if scheme not in {"azblob", "azure"}:
        return None
    parsed = urlparse(uri)
    container = (parsed.netloc or "").strip()
    blob = (parsed.path or "").lstrip("/")
    if not container or not blob:
        return None
    return container, blob


async def resolve_ingest_uri(
    *,
    uri: str,
    filename_hint: str | None = None,
    provider: StorageProviderRecord | None = None,
) -> DownloadedFile:
    uri = uri.strip()
    if not uri:
        raise ValueError("uri must not be empty")

    direct = _gdrive_direct_download_url(uri)
    if direct:
        downloaded = await download_url(direct)
        if filename_hint:
            return DownloadedFile(
                filename=filename_hint,
                content=downloaded.content,
                url=downloaded.url,
            )
        return downloaded

    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"}:
        downloaded = await download_url(uri)
        if filename_hint:
            return DownloadedFile(
                filename=filename_hint,
                content=downloaded.content,
                url=downloaded.url,
            )
        return downloaded

    s3_parts = _parse_s3_uri(uri)
    if s3_parts:
        return await _download_s3(bucket=s3_parts[0], key=s3_parts[1], provider=provider)

    az_parts = _parse_azblob_uri(uri)
    if az_parts:
        return await _download_azure_blob(
            container=az_parts[0],
            blob=az_parts[1],
            provider=provider,
        )

    raise ValueError(f"Unsupported uri scheme: {parsed.scheme or '<none>'}")


async def resolve_provider(store: StorageProviderStore, provider_id: str | None) -> StorageProviderRecord | None:
    if not provider_id:
        return None
    record = store.get_provider(provider_id)
    if record is None:
        raise ValueError(f"Unknown providerId: {provider_id}")
    return record


async def _download_s3(
    *,
    bucket: str,
    key: str,
    provider: StorageProviderRecord | None,
) -> DownloadedFile:
    try:
        import boto3  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ValueError(
            "S3 uri requires boto3 to be installed (pip install boto3) "
            "or provide an https presigned URL instead."
        ) from exc

    cfg = provider.config if provider is not None else {}
    secrets = provider.secrets if provider is not None else {}

    session_kwargs: dict[str, str] = {}
    for key_name in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "region_name"):
        if key_name in secrets and secrets[key_name]:
            session_kwargs[key_name] = str(secrets[key_name])
        elif key_name in cfg and cfg[key_name]:
            session_kwargs[key_name] = str(cfg[key_name])

    s3 = boto3.session.Session(**session_kwargs).client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj.get("Body")
    content = body.read() if body is not None else b""
    filename = key.split("/")[-1] or "s3_object"
    return DownloadedFile(filename=filename, content=content, url=f"s3://{bucket}/{key}")


async def _download_azure_blob(
    *,
    container: str,
    blob: str,
    provider: StorageProviderRecord | None,
) -> DownloadedFile:
    try:
        from azure.storage.blob import BlobServiceClient  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ValueError(
            "Azure blob uri requires azure-storage-blob to be installed "
            "(pip install azure-storage-blob) or provide an https SAS URL instead."
        ) from exc

    if provider is None:
        raise ValueError(
            "Azure blob uri requires providerId with account credentials/config."
        )

    account_url = str(
        provider.config.get("account_url")
        or provider.secrets.get("account_url")
        or ""
    ).strip()
    if not account_url:
        raise ValueError("Azure provider config must include account_url")

    credential = provider.secrets.get("credential") or provider.config.get("credential")
    connection_string = provider.secrets.get("connection_string") or provider.config.get(
        "connection_string"
    )

    if connection_string:
        service = BlobServiceClient.from_connection_string(str(connection_string))
    else:
        service = BlobServiceClient(account_url=account_url, credential=credential)

    blob_client = service.get_blob_client(container=container, blob=blob)
    downloader = blob_client.download_blob()
    content = downloader.readall()
    filename = blob.split("/")[-1] or "azure_blob"
    return DownloadedFile(
        filename=filename,
        content=content,
        url=f"azblob://{container}/{blob}",
    )
