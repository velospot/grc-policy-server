import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"
NULLISH_ENV_VALUES = {"", "null", "none"}


def _is_nullish_env_value(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in NULLISH_ENV_VALUES
    )


def _canonical_env_name(field_name: str, field_info: Any) -> str:
    validation_alias = field_info.validation_alias

    if isinstance(validation_alias, AliasChoices):
        for choice in validation_alias.choices:
            if isinstance(choice, str):
                return choice

    if isinstance(validation_alias, str):
        return validation_alias

    if isinstance(field_info.alias, str):
        return field_info.alias

    return field_name.upper()


def _default_celery_worker_pool() -> str:
    # On macOS, prefork workers can crash with Objective-C/CoreFoundation usage.
    return "solo" if sys.platform == "darwin" else "prefork"


def _default_celery_worker_concurrency() -> int:
    if _default_celery_worker_pool() == "solo":
        return 1
    cpu_count = os.cpu_count() or 1
    # Keep defaults conservative for memory-heavy document pipelines.
    return max(1, min(4, cpu_count))


def _has_nvidia_gpu() -> bool:
    try:
        subprocess.run(
            ["nvidia-smi", "-L"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _default_docling_accelerator_device() -> str:
    # Prefer explicit CUDA on Linux when an NVIDIA GPU is available.
    if _has_nvidia_gpu():
        return "cuda"
    # Keep automatic selection elsewhere (including Apple Silicon / MPS).
    return "auto"


def _default_docling_accelerator_threads() -> int:
    cpu_count = os.cpu_count() or 4
    return max(2, min(8, cpu_count))


class NullishFilteringSource(PydanticBaseSettingsSource):
    """Drop null-like env values so model defaults remain the fallback."""

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self.source = source

    def get_field_value(self, field, field_name: str):
        return self.source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        self.source._set_current_state(self.current_state)
        self.source._set_settings_sources_data(self.settings_sources_data)
        values = self.source()
        return {
            key: value
            for key, value in values.items()
            if not _is_nullish_env_value(value)
        }


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and `.env`."""

    app_name: str = "grc_policy_server"
    environment: str = "production"
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000
    api_bearer_token: str = "dummy-token"
    cors_allow_origins: str = "*"
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"
    cors_allow_credentials: bool = False

    debug: bool = False

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "grc_policy_server"
    mongodb_collection: str = "documents"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "grc_admin"
    postgres_password: str = "grc_admin"
    postgres_db: str = "grc_db"
    database_url: str | None = None

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    neo4j_database: str = "neo4j"
    neo4j_enabled: bool = False

    weaviate_url: str = "http://weaviate:8080"
    weaviate_collection: str = "PolicyChunk"
    weaviate_embedded: bool = False
    weaviate_api_key: str | None = None
    weaviate_grpc_host: str | None = None
    weaviate_grpc_port: int | None = None
    weaviate_grpc_secure: bool | None = None
    weaviate_vectorizer: str = "huggingface"  # "huggingface" | "ollama"
    weaviate_huggingface_endpoint_url: str | None = None
    weaviate_huggingface_model: str = "Qwen/Qwen3-Embedding-0.6B"

    ollama_url: str = "http://localhost:11434"
    ollama_embedding_url: str = "http://localhost:11434"
    ollama_chat_model: str = Field(
        default="granite3.3:8b",
        validation_alias=AliasChoices("OLLAMA_CHAT_MODEL", "OLLAMA_GENERATION_MODEL"),
    )
    ollama_embed_model: str = Field(
        default="qwen3-embedding:0.6b",
        validation_alias=AliasChoices("OLLAMA_EMBED_MODEL", "OLLAMA_EMBEDDING_MODEL"),
    )
    ollama_timeout_sec: float = 180.0

    llm_primary_provider: str = "vllm"  # "vllm" | "ollama"
    vllm_enabled: bool = True
    vllm_chat_url: str = "http://localhost:8001"
    vllm_embed_url: str = "http://localhost:8001"
    vllm_api_key: str | None = None
    vllm_chat_model: str = "ibm-granite/granite-3.3-8b-instruct"
    vllm_embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    vllm_connect_timeout_sec: float = 2.0
    vllm_timeout_sec: float = 180.0
    vllm_max_retries: int = 1

    opik_enabled: bool = False
    opik_url_override: str = "http://localhost:5173/api"
    opik_project_name: str = "grc-policy-server"
    opik_workspace: str = "default"

    @property
    def ollama_generation_model(self) -> str:
        return self.ollama_chat_model

    @property
    def ollama_embedding_model(self) -> str:
        return self.ollama_embed_model

    embed_batch_size: int = 32
    semantic_extraction_batch_size: int = 8
    download_timeout_seconds: float = 30.0
    max_download_mb: int = 50
    upload_root: str = ""
    request_mutex_lock_file: str = "/tmp/grc_policy_server.request.lock"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_default_queue: str = "grc_policy_server.upload"
    celery_task_timeout_sec: float = 900.0
    celery_task_soft_time_limit_sec: float = 1200.0
    celery_task_hard_time_limit_sec: float = 1500.0
    celery_worker_ping_timeout_sec: float = 1.0
    celery_enforce_worker_ping: bool = False
    celery_worker_concurrency: int = _default_celery_worker_concurrency()
    celery_worker_pool: str = _default_celery_worker_pool()
    celery_worker_prefetch_multiplier: int = 1
    celery_worker_max_tasks_per_child: int = 200
    celery_worker_max_memory_per_child_kb: int = 0
    celery_broker_connection_retry_on_startup: bool = True
    celery_broker_pool_limit: int = 10
    celery_result_expires_sec: int = 86400
    celery_task_track_started: bool = True
    celery_task_reject_on_worker_lost: bool = True
    celery_worker_disable_rate_limits: bool = True
    docling_accelerator_device: str = _default_docling_accelerator_device()
    docling_accelerator_threads: int = _default_docling_accelerator_threads()
    docling_cuda_use_flash_attention2: bool = False
    ocr_fallback_enabled: bool = True
    ocr_fallback_min_chars_per_page: int = 80
    ocr_fallback_min_total_chars: int = 250
    ocr_fallback_render_dpi: int = 180
    ocr_fallback_languages: str = "eng+deu+fra+spa"
    ocr_fallback_page_segmentation_mode: int = 6

    @model_validator(mode="after")
    def populate_database_url(self) -> "Settings":
        if not _is_nullish_env_value(self.database_url):
            return self

        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_db, safe="")
        self.database_url = (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )
        return self

    def as_env_items(self) -> Mapping[str, Any]:
        values = self.model_dump()
        return {
            _canonical_env_name(field_name, field_info): values[field_name]
            for field_name, field_info in type(self).model_fields.items()
        }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        return (
            init_settings,
            NullishFilteringSource(env_settings),
            NullishFilteringSource(dotenv_settings),
            file_secret_settings,
        )

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )


settings = Settings()
