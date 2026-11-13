from app.core.config import Settings


def test_database_url_is_built_from_parts() -> None:
    settings = Settings(
        _env_file=None,
        postgres_host="db",
        postgres_port=6543,
        postgres_db="x",
        postgres_user="u",
        postgres_password="p@ss",
    )
    assert settings.database_url == "postgresql+asyncpg://u:p@ss@db:6543/x"


def test_cors_origins_parsed_from_json(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["http://a", "http://b"]')
    assert Settings(_env_file=None).cors_origins == ["http://a", "http://b"]
