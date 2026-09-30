import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    TARGET_APP_URL: str = "http://localhost:3001"
    MISTRAL_API_KEY: str = "your_mistral_api_key_here"
    MISTRAL_MODEL: str = "mistral-small-latest"
    DATABASE_URL: str = "sqlite+aiosqlite:///./orchestration.db"
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 8000
    MAX_CONCURRENT_RUNS: int = 2
    DISCOVERY_MAX_STEPS: int = 15
    MAX_LLM_REQUESTS_PER_RUN: int = 15
    MAX_DISCOVERY_DURATION_SECONDS: int = 180
    DISCOVERY_OBSERVATION_MAX_ELEMENTS: int = 60
    MISTRAL_TIMEOUT_SECONDS: float = 30.0
    MISTRAL_MAX_RETRIES: int = 1
    MISTRAL_RETRY_BACKOFF_SECONDS: float = 1.0
    MISTRAL_RETRY_MAX_BACKOFF_SECONDS: float = 5.0
    MISTRAL_MAX_RESPONSE_BYTES: int = 16384
    SAFETY_ALLOWED_DOMAINS: str = "localhost,127.0.0.1"
    SAFETY_ALLOW_RISKY_ACTIONS: bool = False
    EVIDENCE_DIR: str = "./evidence"
    APEX_OTEL_ENABLED: bool = False
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "apex-automation"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"
    APEX_AUTH_ENABLED: bool = True
    APEX_JWT_SIGNING_KEY: str = ""
    APEX_JWT_ISSUER: str = "apex-automation"
    APEX_JWT_AUDIENCE: str = "apex-automation-api"
    APEX_JWT_TTL_MINUTES: int = 60
    APEX_BOOTSTRAP_ADMIN_USERNAME: str = ""
    APEX_BOOTSTRAP_ADMIN_FULL_NAME: str = ""
    APEX_BOOTSTRAP_ADMIN_EMAIL: str = ""
    APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH: str = ""
    APEX_HANDOFF_SESSION_TTL_MINUTES: int = 120
    APEX_AUTH_COOKIE_SECURE: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def allowed_domains_list(self) -> List[str]:
        return [d.strip() for d in self.SAFETY_ALLOWED_DOMAINS.split(",") if d.strip()]

settings = Settings()

os.makedirs(settings.EVIDENCE_DIR, exist_ok=True)
os.makedirs(os.path.join(settings.EVIDENCE_DIR, "discovery"), exist_ok=True)
os.makedirs(os.path.join(settings.EVIDENCE_DIR, "artifacts"), exist_ok=True)
os.makedirs(os.path.join(settings.EVIDENCE_DIR, "replay"), exist_ok=True)
os.makedirs(os.path.join(settings.EVIDENCE_DIR, "escalations"), exist_ok=True)
