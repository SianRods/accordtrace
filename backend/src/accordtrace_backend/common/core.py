"""Configuration and stable identifiers; no service access at import time."""
import hashlib
import uuid
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]
NAMESPACE = uuid.UUID('da64cd55-3b0c-491a-9fb5-0328da68cf08')
PARSER_VERSION = 'structural-v3'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(*parts: object) -> str:
    import json
    return str(uuid.uuid5(NAMESPACE, json.dumps(parts, ensure_ascii=True)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='ACCORDTRACE_', extra='ignore')
    backend_root: Path = ROOT
    source_dir: Path = ROOT.parent / 'data/maud/MAUD_v1/contracts'
    database_url: SecretStr = SecretStr('postgresql+psycopg://localhost/accordtrace')
    neo4j_uri: str = 'bolt://localhost:7687'
    neo4j_user: str = 'neo4j'
    neo4j_password: SecretStr = SecretStr('')
    qdrant_url: str = 'http://localhost:6333'
    api_token: SecretStr = SecretStr('')
    lease_seconds: int = Field(120, ge=30, le=3600)
    # Compatibility flag: embeddings are now local-only; false fails configuration.
    offline_mode: bool = True
    ollama_url: str = 'http://127.0.0.1:11434'
    ollama_embedding_model: str = 'embeddinggemma:300m'
    ollama_embedding_timeout_seconds: int = Field(60,ge=1,le=180)
    ollama_keep_alive: str = '30m'
    approved_manifests: tuple[str, ...] = ()
    approved_manifest_dir: Path|None = None
    groq_api_key: SecretStr = SecretStr('')
    groq_free_tier_confirmed: bool = False
    local_principal_id: str = Field('local',min_length=1,max_length=100)
    chat_active_seconds: int = Field(30,ge=1,le=120)
    chat_lifetime_seconds: int = Field(600,ge=30,le=3600)
    chat_provider_timeout_seconds: int = Field(20,ge=1,le=60)
    chat_groq_rpm: int = Field(30,ge=1)
    chat_groq_tpm: int = Field(8000,ge=1)
    chat_groq_rpd: int = Field(1000,ge=1)
    chat_groq_tpd: int = Field(200000,ge=1)

    @field_validator('offline_mode')
    @classmethod
    def require_local_embeddings(cls,value):
        if not value:raise ValueError('Embeddings are local-only; offline_mode must be true')
        return value

    @property
    def runtime(self) -> Path:
        return self.backend_root / '.runtime'

    @property
    def manifest(self) -> Path:
        return self.backend_root / 'configs/corpus.initial.yaml'


class DomainError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)
