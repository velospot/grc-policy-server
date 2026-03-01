from pathlib import Path

from grc_policy_server.core.config import ENV_FILE, Settings


def clear_runtime_env(monkeypatch) -> None:
    for key in (
        "PORT",
        "UPLOAD_ROOT",
        "WEAVIATE_URL",
        "OLLAMA_CHAT_MODEL",
        "OLLAMA_GENERATION_MODEL",
        "OLLAMA_EMBED_MODEL",
        "OLLAMA_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_dotenv_values_override_defaults(tmp_path, monkeypatch):
    clear_runtime_env(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "PORT=8500\n"
        "UPLOAD_ROOT=./data/uploads\n"
        "WEAVIATE_URL=http://localhost:8080\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.port == 8500
    assert settings.upload_root == "./data/uploads"
    assert settings.weaviate_url == "http://localhost:8080"


def test_nullish_dotenv_values_fall_back_to_defaults(tmp_path, monkeypatch):
    clear_runtime_env(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "PORT=\n"
        "WEAVIATE_URL=null\n"
        "OLLAMA_CHAT_MODEL=none\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.port == 8000
    assert settings.weaviate_url == "http://weaviate:8080"
    assert settings.ollama_chat_model == "granite3.3:8b"


def test_process_environment_overrides_dotenv(tmp_path, monkeypatch):
    clear_runtime_env(monkeypatch)
    monkeypatch.setenv("PORT", "9100")

    env_file = tmp_path / ".env"
    env_file.write_text("PORT=8500\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.port == 9100


def test_as_env_items_uses_effective_runtime_values(tmp_path, monkeypatch):
    clear_runtime_env(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "PORT=8500\n"
        "OLLAMA_GENERATION_MODEL=granite-debug\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    env_items = settings.as_env_items()

    assert env_items["PORT"] == 8500
    assert env_items["OLLAMA_CHAT_MODEL"] == "granite-debug"
    assert "OLLAMA_GENERATION_MODEL" not in env_items


def test_default_env_file_path_is_absolute():
    assert isinstance(ENV_FILE, Path)
    assert ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == ENV_FILE
