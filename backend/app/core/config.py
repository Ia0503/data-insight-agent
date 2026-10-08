import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("NEWAI_ENV_FILE", ROOT / ".env"),
        extra="ignore",
        hide_input_in_errors=True,
    )
    database_url: SecretStr
    # 容器只覆盖主机/端口，保留 URL 编码，避免插值含特殊字符的密码。
    database_host: str = ""
    database_port: int | None = Field(default=None, ge=1, le=65535)
    upload_dir: Path = ROOT / "runtime" / "uploads"
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    embedding_model: str = "BAAI/bge-base-zh-v1.5"
    embedding_revision: str = "f03589ceff5aac7111bd60cfc7d497ca17ecac65"
    embedding_dir: Path = ROOT / "runtime" / "models" / "bge-base-zh-v1.5"
    embedding_dimension: int = Field(default=768, ge=1, le=4096)
    embedding_pooling: str = Field(default="cls", pattern="^(cls|mean|last)$")
    embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    embedding_document_prefix: str = ""
    embedding_max_tokens: int = Field(default=512, ge=64, le=32768)
    embedding_timeout_seconds: int = Field(default=60, ge=5, le=300)
    chunk_tokens: int = Field(default=300, ge=32, le=8192)
    chunk_overlap: int = Field(default=40, ge=0, le=512)
    # 联调由用户明确开启；仅填写密钥不会自动触发计费调用。
    llm_calls_enabled: bool = False
    llm_provider: str = ""
    llm_thinking_enabled: bool = False
    llm_timeout_seconds: int = Field(default=60, ge=5, le=120)
    agent_timeout_seconds: int = Field(default=300, ge=30, le=600)
    analysis_password_required: bool = True
    analysis_password_hash: SecretStr = SecretStr("")
    llm_hourly_token_limit: int = Field(default=1_000_000, ge=1, le=1_000_000_000)
    database_statement_timeout_ms: int = Field(default=15_000, ge=1000, le=120_000)
    llm_aliyun_base_url: str = ""
    llm_aliyun_model: str = ""
    llm_aliyun_api_key: SecretStr = SecretStr("")
    llm_deepseek_base_url: str = ""
    llm_deepseek_model: str = ""
    llm_deepseek_api_key: SecretStr = SecretStr("")
    llm_siliconflow_base_url: str = ""
    llm_siliconflow_model: str = ""
    llm_siliconflow_api_key: SecretStr = SecretStr("")
    llm_volcengine_base_url: str = ""
    llm_volcengine_model: str = ""
    llm_volcengine_api_key: SecretStr = SecretStr("")
    llm_zhipu_base_url: str = ""
    llm_zhipu_model: str = ""
    llm_zhipu_api_key: SecretStr = SecretStr("")
    llm_moonshot_base_url: str = ""
    llm_moonshot_model: str = ""
    llm_moonshot_api_key: SecretStr = SecretStr("")
    llm_custom_base_url: str = ""
    llm_custom_model: str = ""
    llm_custom_api_key: SecretStr = SecretStr("")

    @field_validator("upload_dir", "embedding_dir")
    @classmethod
    def resolve_upload_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value

    @model_validator(mode="after")
    def container_database(self):
        if self.database_host or self.database_port:
            url = make_url(self.database_url.get_secret_value()).set(
                host=self.database_host or None, port=self.database_port
            )
            self.database_url = SecretStr(url.render_as_string(hide_password=False))
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
