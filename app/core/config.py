"""jemodel 后端运行时配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """由环境变量驱动的应用配置。"""

    model_config = SettingsConfigDict(env_prefix="JEMODEL_", env_file=".env", extra="ignore")

    app_name: str = "jemodel"
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/jemodel.db"
    static_dir: Path | None = None
    secret_key: str | None = None
    bootstrap_admin_email: str = "admin@local.jemodel"
    bootstrap_admin_name: str = "Local Admin"
    bootstrap_admin_api_key: str | None = None
    control_session_ttl_seconds: int = 60 * 60 * 12
    experimental_codex_oauth: bool = True
    codex_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    codex_token_refresh_lead_seconds: int = 432000
    mock_upstreams: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回用于依赖注入的缓存配置对象。"""
    return Settings()
