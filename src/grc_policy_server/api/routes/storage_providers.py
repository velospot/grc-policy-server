from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from grc_policy_server.api.deps import get_storage_provider_store, require_api_bearer_token
from grc_policy_server.models.schemas import (
    StorageProviderConfig,
    StorageProviderConfigCreateRequest,
    StorageProviderConfigUpdateRequest,
    StorageProviderListResponse,
)
from grc_policy_server.services.storage.storage_provider_store import (
    StorageProviderStore,
)

router = APIRouter(
    prefix="/storage/providers",
    tags=["storage"],
    dependencies=[Depends(require_api_bearer_token)],
)


@router.get(
    "",
    response_model=StorageProviderListResponse,
    summary="List configured storage providers",
)
def list_storage_providers(
    store: StorageProviderStore = Depends(get_storage_provider_store),
):
    providers = [
        StorageProviderConfig.model_validate(item.to_public_dict())
        for item in store.list_providers()
    ]
    return StorageProviderListResponse(providers=providers)


@router.post(
    "",
    response_model=StorageProviderConfig,
    status_code=status.HTTP_201_CREATED,
    summary="Create a storage provider configuration",
)
def create_storage_provider(
    payload: StorageProviderConfigCreateRequest,
    store: StorageProviderStore = Depends(get_storage_provider_store),
):
    provider_id = str(uuid4())
    record = store.upsert_provider(
        provider_id=provider_id,
        provider_type=payload.providerType,
        name=payload.name.strip(),
        config=dict(payload.config or {}),
        secrets=dict(payload.secrets or {}),
    )
    return StorageProviderConfig.model_validate(record.to_public_dict())


@router.get(
    "/{provider_id}",
    response_model=StorageProviderConfig,
    summary="Get a storage provider configuration",
)
def get_storage_provider(
    provider_id: str,
    store: StorageProviderStore = Depends(get_storage_provider_store),
):
    record = store.get_provider(provider_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return StorageProviderConfig.model_validate(record.to_public_dict())


@router.put(
    "/{provider_id}",
    response_model=StorageProviderConfig,
    summary="Update a storage provider configuration",
)
def update_storage_provider(
    provider_id: str,
    payload: StorageProviderConfigUpdateRequest,
    store: StorageProviderStore = Depends(get_storage_provider_store),
):
    existing = store.get_provider(provider_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    record = store.upsert_provider(
        provider_id=provider_id,
        provider_type=existing.provider_type,
        name=(payload.name.strip() if payload.name is not None else existing.name),
        config=dict(payload.config) if payload.config is not None else existing.config,
        secrets=dict(payload.secrets) if payload.secrets is not None else existing.secrets,
    )
    return StorageProviderConfig.model_validate(record.to_public_dict())


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a storage provider configuration",
)
def delete_storage_provider(
    provider_id: str,
    store: StorageProviderStore = Depends(get_storage_provider_store),
):
    deleted = store.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return None

