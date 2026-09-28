from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://support:support@localhost:5432/support"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    hindsight_url: str = "http://localhost:8888"
    hindsight_api_key: str = ""
    fernet_key: str = ""
    frustration_escalation_threshold: float = 0.75

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
