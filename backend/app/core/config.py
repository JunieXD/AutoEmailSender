from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    uploads_dir: Path
    crawler_debug_dir: Path
    database_url: str
    draft_worker_interval_seconds: int
    dispatcher_interval_seconds: int
    imap_poll_interval_seconds: int
    match_analysis_job_worker_count: int
    match_analysis_job_interval_seconds: int
    match_analysis_job_item_concurrency: int
    crawler_worker_count: int
    crawler_profile_enrichment_concurrency: int
    crawler_host_concurrency: int
    crawler_profile_fetch_max_retries: int
    llm_request_timeout_seconds: int
    smtp_send_timeout_seconds: int
    imap_lookback_hours: int
    operation_log_retention_days: int
    enable_background_workers: bool
    crawler_debug_enabled: bool


def _get_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///") and "+aiosqlite" not in database_url:
        return database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return database_url


def _resolve_data_dir() -> Path:
    raw_value = os.getenv("AUTO_EMAIL_SENDER_DATA_DIR")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_DATA_DIR
    return Path(raw_value).expanduser().resolve()


def _build_default_database_url(data_dir: Path) -> str:
    return f"sqlite+aiosqlite:///{(data_dir / 'auto_email_sender.db').as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = _resolve_data_dir()
    uploads_dir = data_dir / "uploads"
    data_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    crawler_debug_dir = Path(
        os.getenv("CRAWLER_DEBUG_DIR", (data_dir / "logs" / "crawler").as_posix())
    )
    crawler_debug_dir.mkdir(parents=True, exist_ok=True)
    database_url = _normalize_database_url(
        os.getenv("DATABASE_URL", _build_default_database_url(data_dir)),
    )
    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        uploads_dir=uploads_dir,
        crawler_debug_dir=crawler_debug_dir,
        database_url=database_url,
        draft_worker_interval_seconds=_get_int_env("DRAFT_WORKER_INTERVAL_SECONDS", 10),
        dispatcher_interval_seconds=_get_int_env("DISPATCHER_INTERVAL_SECONDS", 30),
        imap_poll_interval_seconds=_get_int_env("IMAP_POLL_INTERVAL_SECONDS", 60),
        match_analysis_job_worker_count=_get_int_env("MATCH_ANALYSIS_JOB_WORKER_COUNT", 1),
        match_analysis_job_interval_seconds=_get_int_env("MATCH_ANALYSIS_JOB_INTERVAL_SECONDS", 10),
        match_analysis_job_item_concurrency=_get_int_env("MATCH_ANALYSIS_JOB_ITEM_CONCURRENCY", 5),
        crawler_worker_count=_get_int_env("CRAWLER_WORKER_COUNT", 8),
        crawler_profile_enrichment_concurrency=_get_int_env(
            "CRAWLER_PROFILE_ENRICHMENT_CONCURRENCY",
            3,
        ),
        crawler_host_concurrency=_get_int_env("CRAWLER_HOST_CONCURRENCY", 2),
        crawler_profile_fetch_max_retries=_get_int_env(
            "CRAWLER_PROFILE_FETCH_MAX_RETRIES",
            2,
        ),
        llm_request_timeout_seconds=_get_int_env("LLM_REQUEST_TIMEOUT_SECONDS", 120),
        smtp_send_timeout_seconds=_get_int_env("SMTP_SEND_TIMEOUT_SECONDS", 30),
        imap_lookback_hours=_get_int_env("IMAP_LOOKBACK_HOURS", 72),
        operation_log_retention_days=_get_int_env("OPERATION_LOG_RETENTION_DAYS", 30),
        enable_background_workers=_get_bool_env("ENABLE_BACKGROUND_WORKERS", True),
        crawler_debug_enabled=_get_bool_env("CRAWLER_DEBUG", True),
    )


def get_sync_database_url() -> str:
    return get_settings().database_url.replace("+aiosqlite", "", 1)
