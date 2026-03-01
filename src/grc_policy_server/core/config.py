from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "your_password"
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
<<<<<<< HEAD
    upload_root: str = "/Users/aruntejasriramula/ChatEnginePDF/grc-policy-server/data/uploads"
=======
    upload_root: str = "./data/uploads"
>>>>>>> fixes-functional-weaviate-docling

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
