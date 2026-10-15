"""Application settings, loaded from environment variables or a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Never hard-code secrets: they come from the environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    query_timeout_s: float = 10.0

    # If set, POST /api/ask requires the header X-API-Key with this value.
    api_key: str = ""

    # NL->Cypher agent (app/agent), any OpenAI-compatible chat endpoint. If unset,
    # POST /api/ask answers 503 and the rest of the API still works.
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # Classifier trained by scripts/gds_setup.py (Person 3).
    model_path: str = "models/tier_classifier.joblib"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return one cached Settings instance per process."""
    return Settings()
