from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "grc_policy_server"
    environment: str = "production"
    log_level: str = "INFO"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Feature flags / runtime behavior
    debug: bool = True

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "grc_policy_server"
    mongodb_collection: str = "documents"

    # Weaviate
    weaviate_url: str = "http://weaviate:8080"
    weaviate_collection: str = "PolicyChunk"
    weaviate_embedded: bool = False

    # Ollama
    ollama_url: str = "http://ollama:11434"
    ollama_embedding_model: str = "granite-embedding:278m"
    ollama_generation_model: str = "llama3.1"
    embed_batch_size: int = 32
    # Download
    download_timeout_seconds: float = 30.0
    max_download_mb: int = 50
    upload_root: str = "/data/uploads"
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",  # no APP_ prefix unless you want one
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
