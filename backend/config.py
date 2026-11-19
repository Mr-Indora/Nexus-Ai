"""Central configuration for Nexus AI V2."""
import json
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Load backend/.env
load_dotenv(BASE_DIR / ".env")
_dotenv_settings = dotenv_values(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in (
        "1", "true", "yes", "on"
    )


def _load_client_json() -> dict:
    """
    Load Google OAuth credentials from client_secret.json.
    Supports both Web and Installed OAuth clients.
    """
    configured_path = os.getenv("GOOGLE_CLIENT_SECRET_FILE")

    path = Path(configured_path) if configured_path else BASE_DIR / "client_secret.json"

    if not path.is_absolute():
        path = BASE_DIR / path

    if not path.exists():
        print(f"[OAuth] Client JSON not found: {path}")
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))

        client = data.get("web") or data.get("installed") or {}

        if not isinstance(client, dict):
            return {}

        print("[OAuth] Google client JSON loaded successfully.")
        return client

    except (json.JSONDecodeError, OSError) as exc:
        print(f"[OAuth] Failed to load client JSON: {exc}")
        return {}


_google_client = _load_client_json()


class Settings:

    # Environment variables take priority over JSON.
    GOOGLE_CLIENT_ID = (
        os.getenv("GOOGLE_CLIENT_ID")
        or _google_client.get("client_id", "")
    )

    GOOGLE_CLIENT_SECRET = (
        os.getenv("GOOGLE_CLIENT_SECRET")
        or _google_client.get("client_secret", "")
    )

    # IMPORTANT: Browser callback comes through Next.js port 3000.
    GOOGLE_REDIRECT_URI = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:3000/api/auth/google/callback"
    )
    # Like the Gemini setting below, ignore blank inherited variables so a
    # populated backend/.env remains usable when the launcher exports empty
    # placeholders for optional integrations.
    LINKEDIN_CLIENT_ID = (
        os.getenv("LINKEDIN_CLIENT_ID", "").strip()
        or str(_dotenv_settings.get("LINKEDIN_CLIENT_ID") or "").strip()
    )
    LINKEDIN_CLIENT_SECRET = (
        os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
        or str(_dotenv_settings.get("LINKEDIN_CLIENT_SECRET") or "").strip()
    )
    LINKEDIN_REDIRECT_URI = (
        os.getenv("LINKEDIN_REDIRECT_URI", "").strip()
        or str(_dotenv_settings.get("LINKEDIN_REDIRECT_URI") or "").strip()
        or "http://localhost:3000/api/auth/linkedin/callback"
    )
    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

    # A blank inherited environment variable should not mask a valid local key.
    # A non-empty process environment value still takes precedence over .env.
    GEMINI_API_KEY = (
        os.getenv("GEMINI_API_KEY", "").strip()
        or str(_dotenv_settings.get("GEMINI_API_KEY") or "").strip()
    )
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    # First Gmail connection is backfilled in bounded batches so a large mailbox
    # can resume over multiple syncs instead of silently stopping at 30 messages.
    GMAIL_INITIAL_SYNC_MAX_EMAILS = int(os.getenv("GMAIL_INITIAL_SYNC_MAX_EMAILS", "500"))
    GMAIL_INITIAL_SYNC_DAYS = int(os.getenv("GMAIL_INITIAL_SYNC_DAYS", "0"))

    # Local Qwen3 GGUF runtime. Disabled by default so existing deployments
    # remain deterministic until the user explicitly installs a model.
    LOCAL_LLM_ENABLED = _bool("LOCAL_LLM_ENABLED", False)
    LOCAL_LLM_MODE = os.getenv("LOCAL_LLM_MODE", "disabled").strip().lower()
    LOCAL_LLM_PROVIDER = os.getenv("LOCAL_LLM_PROVIDER", "llama.cpp").strip().lower()
    LOCAL_LLM_MODEL_PATH = os.getenv(
        "LOCAL_LLM_MODEL_PATH",
        str(BASE_DIR / "models" / "qwen3-0.6b-instruct-q4_k_m.gguf"),
    )
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b").strip()
    OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "5m").strip()
    LOCAL_LLM_CONTEXT = int(os.getenv(
        "LOCAL_LLM_CONTEXT", "4096" if LOCAL_LLM_PROVIDER == "ollama" else "1024"
    ))
    LOCAL_LLM_THREADS = int(os.getenv("LOCAL_LLM_THREADS", str(max(1, min((os.cpu_count() or 2) - 1, 4)))))
    LOCAL_LLM_BATCH = int(os.getenv("LOCAL_LLM_BATCH", "128"))
    LOCAL_LLM_MAX_TOKENS = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "256"))
    ASSISTANT_CLOUD_FALLBACK = _bool("ASSISTANT_CLOUD_FALLBACK", True)

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    FRONTEND_URL = os.getenv(
        "FRONTEND_URL",
        "http://localhost:3000"
    ).rstrip("/")
    FRONTEND_ORIGINS = tuple(
        origin.strip().rstrip("/")
        for origin in os.getenv("FRONTEND_ORIGINS", FRONTEND_URL).split(",")
        if origin.strip()
    )

    TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "sqlite:///./nexus.db"
    )

    _pids_database_path = BASE_DIR.parent / "pids-isolated-work" / "pids-runtime.db"
    PIDS_DATABASE_URL = os.getenv(
        "PIDS_DATABASE_URL",
        f"sqlite:///{_pids_database_path.as_posix()}",
    )
    PIDS_ENABLED = _bool("PIDS_ENABLED", _pids_database_path.is_file())
    PIDS_RAW_RETENTION_DAYS = int(os.getenv("PIDS_RAW_RETENTION_DAYS", "0"))
    PIDS_VAULT_DIR = os.getenv("PIDS_VAULT_DIR", str(BASE_DIR.parent / "pids-isolated-work" / "raw-vault"))
    PIDS_VAULT_KEY_PATH = os.getenv("PIDS_VAULT_KEY_PATH", str(BASE_DIR.parent / "pids-isolated-work" / "vault-keys.json"))

    DEMO_MODE = _bool("DEMO_MODE", True)

    SESSION_DAYS = 7

    SCOPES = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/classroom.courses.readonly",
        "https://www.googleapis.com/auth/classroom.announcements.readonly",
        "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
        "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    ]

    @property
    def google_configured(self) -> bool:
        return bool(
            self.GOOGLE_CLIENT_ID
            and self.GOOGLE_CLIENT_SECRET
        )

    @property
    def linkedin_configured(self) -> bool:
        return bool(self.LINKEDIN_CLIENT_ID and self.LINKEDIN_CLIENT_SECRET and self.LINKEDIN_REDIRECT_URI)

    def validate(self) -> None:
        """Reject unsafe production configuration while preserving demo defaults."""
        if not self.DEMO_MODE and (
            self.SECRET_KEY == "dev-secret-change-me" or len(self.SECRET_KEY) < 32
        ):
            raise RuntimeError("SECRET_KEY must be at least 32 characters when DEMO_MODE is false")

        if not self.DATABASE_URL:
            raise RuntimeError("DATABASE_URL must not be empty")
        if self.LOCAL_LLM_MODE not in {"disabled", "local-first", "local-only"}:
            raise RuntimeError("LOCAL_LLM_MODE must be disabled, local-first, or local-only")
        if self.LOCAL_LLM_PROVIDER not in {"llama.cpp", "ollama"}:
            raise RuntimeError("LOCAL_LLM_PROVIDER must be llama.cpp or ollama")
        if self.LOCAL_LLM_MODE != "disabled" and not self.LOCAL_LLM_ENABLED:
            raise RuntimeError("LOCAL_LLM_ENABLED must be true when LOCAL_LLM_MODE is enabled")
        if self.LOCAL_LLM_CONTEXT < 256 or self.LOCAL_LLM_CONTEXT > 4096:
            raise RuntimeError("LOCAL_LLM_CONTEXT must be between 256 and 4096")
        if not 30 <= self.GMAIL_INITIAL_SYNC_MAX_EMAILS <= 1000:
            raise RuntimeError("GMAIL_INITIAL_SYNC_MAX_EMAILS must be between 30 and 1000")
        if not 0 <= self.GMAIL_INITIAL_SYNC_DAYS <= 36500:
            raise RuntimeError("GMAIL_INITIAL_SYNC_DAYS must be 0 (all mail) or between 1 and 36500")


settings = Settings()
settings.validate()

print(
    "[OAuth] Configuration:",
    "READY" if settings.google_configured else "MISSING CREDENTIALS"
)
