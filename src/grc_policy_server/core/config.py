from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"
NULLISH_ENV_VALUES = {"", "null", "none"}


def _is_nullish_env_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in NULLISH_ENV_VALUES)


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
        return {key: value for key, value in values.items() if not _is_nullish_env_value(value)}


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and `.env`."""

    # App
    app_name: str = "grc_policy_server"
    environment: str = "production"
    log_level: str = "INFO"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    api_bearer_token: str = "dummy-token"
    cors_allow_origins: str = "*"
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"
    cors_allow_credentials: bool = False

    # Feature flags / runtime behavior
    debug: bool = False

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "grc_policy_server"
    mongodb_collection: str = "documents"

    # Neo4j
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    neo4j_database: str = "neo4j"

    # Weaviate
    weaviate_url: str = "http://weaviate:8080"
    weaviate_collection: str = "PolicyChunk"
    weaviate_embedded: bool = False

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_chat_model: str = Field(
        default="granite3.3:8b",
        validation_alias=AliasChoices("OLLAMA_CHAT_MODEL", "OLLAMA_GENERATION_MODEL"),
    )
    ollama_embed_model: str = Field(
        default="qwen3-embedding:0.6b",
        validation_alias=AliasChoices("OLLAMA_EMBED_MODEL", "OLLAMA_EMBEDDING_MODEL"),
    )
    ollama_timeout_sec: float = 180.0

    # Backward-compatible accessors for older internal names.
    @property
    def ollama_generation_model(self) -> str:
        return self.ollama_chat_model

    @property
    def ollama_embedding_model(self) -> str:
        return self.ollama_embed_model

    # Ingestion / retrieval
    embed_batch_size: int = 32
    download_timeout_seconds: float = 30.0
    max_download_mb: int = 50
    upload_root: str = ""

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
