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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",  # no APP_ prefix unless you want one
        case_sensitive=False,
    )


settings = Settings()
