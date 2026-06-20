"""Configuration, environment loading, and logging helpers for garment-leads."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

DEFAULT_GROUP_ID: Final[str] = "284751225383775"
DEFAULT_USER_AGENTS: Final[tuple[str, ...]] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile Safari/604.1",
)
PHONE_LOG_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?:\+|00)?216[\s.\-/()]*)?(?P<prefix>\d{2})[\s.\-/()]*(?P<a>\d{3})[\s.\-/()]*(?P<b>\d{3})"
)


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from defaults and environment variables."""

    group_id: str
    group_url: str
    db_path: Path
    export_dir: Path
    log_dir: Path
    raw_response_dir: Path
    sample_html_path: Path
    request_timeout_seconds: float
    rate_limit_seconds: float
    flask_host: str
    flask_port: int
    user_agents: tuple[str, ...]
    # v3 additions
    fb_cookies_path: Path
    fb_cookie_max_age_days: int
    fb_min_delay_seconds: float
    fb_max_delay_seconds: float
    fb_max_requests_per_minute: int
    fb_max_retries: int


def load_dotenv(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE pairs from a local .env file without overriding env vars."""

    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def project_root() -> Path:
    """Return the current project root used for default relative paths."""

    return Path.cwd()


def get_settings() -> Settings:
    """Create a Settings object from environment variables and defaults."""

    load_dotenv()
    root = project_root()
    group_id = os.getenv("GARMENT_LEADS_GROUP_ID", DEFAULT_GROUP_ID)
    group_url = os.getenv("GARMENT_LEADS_GROUP_URL", f"https://mbasic.facebook.com/groups/{group_id}/")
    user_agents_env = os.getenv("GARMENT_LEADS_USER_AGENTS", "")
    user_agents = tuple(item.strip() for item in user_agents_env.split("|") if item.strip()) or DEFAULT_USER_AGENTS
    return Settings(
        group_id=group_id,
        group_url=group_url,
        db_path=Path(os.getenv("GARMENT_LEADS_DB_PATH", str(root / "garment_leads.sqlite3"))),
        export_dir=Path(os.getenv("GARMENT_LEADS_EXPORT_DIR", str(root / "exports"))),
        log_dir=Path(os.getenv("GARMENT_LEADS_LOG_DIR", str(root / "logs"))),
        raw_response_dir=Path(os.getenv("GARMENT_LEADS_RAW_RESPONSE_DIR", str(root / "raw_responses"))),
        sample_html_path=Path(
            os.getenv(
                "GARMENT_LEADS_SAMPLE_HTML",
                str(Path(__file__).resolve().parent / "sample_group.html"),
            )
        ),
        request_timeout_seconds=float(os.getenv("GARMENT_LEADS_REQUEST_TIMEOUT", "15")),
        rate_limit_seconds=float(os.getenv("GARMENT_LEADS_RATE_LIMIT", "1.0")),
        flask_host=os.getenv("FLASK_HOST", "127.0.0.1"),
        flask_port=int(os.getenv("FLASK_PORT", "5001")),
        user_agents=user_agents,
        # v3
        fb_cookies_path=Path(
            os.getenv(
                "GARMENT_LEADS_FB_COOKIES_PATH",
                str(
                    Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
                    / "garment-leads"
                    / "fb_cookies.json"
                ),
            )
        ),
        fb_cookie_max_age_days=int(os.getenv("GARMENT_LEADS_FB_COOKIE_MAX_AGE_DAYS", "30")),
        fb_min_delay_seconds=float(os.getenv("GARMENT_LEADS_FB_MIN_DELAY", "2.0")),
        fb_max_delay_seconds=float(os.getenv("GARMENT_LEADS_FB_MAX_DELAY", "8.0")),
        fb_max_requests_per_minute=int(os.getenv("GARMENT_LEADS_FB_MAX_RPM", "8")),
        fb_max_retries=int(os.getenv("GARMENT_LEADS_FB_MAX_RETRIES", "3")),
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    """Create directories used for exports, logs, and saved raw responses."""

    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_response_dir.mkdir(parents=True, exist_ok=True)
    if settings.db_path.parent:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)


def mask_phone(value: str) -> str:
    """Mask phone-like strings for safe logs while keeping enough shape for debugging."""

    def repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        country = "+216 " if match.group(0).lstrip().startswith(("+216", "00216", "216")) else ""
        return f"{country}{prefix} XXX XXX"

    return PHONE_LOG_RE.sub(repl, value)


class PIIMaskingFilter(logging.Filter):
    """Logging filter that masks phone-like values in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_phone(record.msg)
        if record.args:
            record.args = tuple(mask_phone(str(arg)) for arg in record.args)
        return True


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    """Configure and return the package logger with a rotating file handler."""

    current_settings = settings or get_settings()
    ensure_runtime_dirs(current_settings)
    logger = logging.getLogger("garment_leads")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = current_settings.log_dir / "scraper.log"
    if not any(isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == str(log_path) for handler in logger.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(PIIMaskingFilter())
        logger.addHandler(handler)
    return logger
