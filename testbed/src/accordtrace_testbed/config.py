"""Independent settings; never read the application's .env implicitly."""
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='TESTBED_', env_file=ROOT/'.env', extra='ignore')
    backend_url: str = 'http://127.0.0.1:8000'
    backend_api_token: SecretStr = SecretStr('')
    source_dir: Path = Path('../data/maud/MAUD_v1/contracts')
    ollama_url: str = 'http://127.0.0.1:11434'
    ollama_model: str = 'embeddinggemma:300m'
    gemini_api_key: SecretStr = SecretStr('')
    gemini_model: str = ''
    gemini_free_tier_confirmed: bool = False
    judge_requests_per_minute: int = Field(2, ge=1)
    judge_max_calls: int = Field(40, ge=1)
    request_timeout_seconds: int = Field(30, ge=1, le=120)
    chat_wait_seconds: int = Field(660, ge=1, le=900)

    @field_validator('backend_url')
    @classmethod
    def backend_endpoint(cls, value):
        u = urlsplit(value)
        if u.username or u.password or u.scheme not in ('http', 'https') or u.query or u.fragment or u.path not in ('','/'):
            raise ValueError('Invalid backend origin')
        if u.scheme != 'https' and u.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Nonlocal backend requires HTTPS')
        return value.rstrip('/')

    @field_validator('ollama_url')
    @classmethod
    def local_embeddings(cls, value):
        u = urlsplit(value)
        if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1') or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
            raise ValueError('Evaluation embeddings must use local Ollama')
        return value.rstrip('/')

    @property
    def sources(self):
        return self.source_dir if self.source_dir.is_absolute() else (ROOT/self.source_dir).resolve()
