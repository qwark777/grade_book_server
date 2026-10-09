import os
import secrets
import warnings
from cryptography.fernet import Fernet

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_local_env() -> None:
    if os.getenv("GRADEBOOK_SKIP_LOCAL_ENV", "").lower() in ("1", "true", "yes"):
        return

    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


_load_local_env()


def _csv_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _get_required_secret(name: str, min_length: int = 32) -> str:
    value = os.getenv(name)
    if value and len(value) >= min_length:
        return value

    if os.getenv("ALLOW_INSECURE_DEV_DEFAULTS", "").lower() in ("1", "true", "yes"):
        warnings.warn(
            f"{name} is not set. Using an ephemeral development secret.",
            RuntimeWarning,
            stacklevel=2,
        )
        return secrets.token_urlsafe(min_length)

    raise RuntimeError(
        f"{name} must be set to a strong secret in the environment. "
        "For local-only development, set ALLOW_INSECURE_DEV_DEFAULTS=1."
    )


def _allow_insecure_dev_defaults() -> bool:
    return os.getenv("ALLOW_INSECURE_DEV_DEFAULTS", "").lower() in ("1", "true", "yes")


def _get_required_fernet_key(name: str) -> str:
    value = os.getenv(name)
    if value:
        try:
            Fernet(value.encode())
            return value
        except Exception as exc:
            raise RuntimeError(
                f"{name} must be a valid Fernet key. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

    if _allow_insecure_dev_defaults():
        warnings.warn(
            f"{name} is not set. Using an ephemeral development key.",
            RuntimeWarning,
            stacklevel=2,
        )
        return Fernet.generate_key().decode()

    raise RuntimeError(
        f"{name} must be set to a valid Fernet key in the environment. "
        "For local-only development, set ALLOW_INSECURE_DEV_DEFAULTS=1."
    )


class Settings:
    # JWT Configuration
    SECRET_KEY: str = _get_required_secret("SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 604800  # 7 days
    
    # Encryption
    ENCRYPTION_KEY: str = _get_required_fernet_key("ENCRYPTION_KEY")
    
    # Database Configuration
    # Default to 'localhost' for local run (python main_new.py). In Docker, set MYSQL_HOST=db in docker-compose.
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    # MYSQL_PASSWORD will be set after class definition
    MYSQL_DB: str = os.getenv("MYSQL_DB", "grade_book")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    DB_POOL_MIN_SIZE: int = int(os.getenv("DB_POOL_MIN_SIZE", "5"))
    DB_POOL_MAX_SIZE: int = int(os.getenv("DB_POOL_MAX_SIZE", "30"))
    DB_POOL_RECYCLE_SECONDS: int = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "3600"))
    INIT_DB_ON_STARTUP: bool = os.getenv(
        "INIT_DB_ON_STARTUP", ""
    ).lower() in ("1", "true", "yes")
    
    # File Storage
    BASE_DIR: str = BASE_DIR
    PROFILE_PHOTOS_DIR: str = os.path.join(BASE_DIR, "profile_photos")
    ACHIEVEMENTS_DIR: str = os.path.join(BASE_DIR, "achievements")
    HOMEWORK_SUBMISSIONS_DIR: str = os.path.join(BASE_DIR, "homework_submissions")
    HOMEWORK_ATTACHMENTS_DIR: str = os.path.join(BASE_DIR, "homework_attachments")
    MESSAGE_ATTACHMENTS_DIR: str = os.path.join(BASE_DIR, "message_attachments")
    
    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Grade Book API"
    MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "240"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "15"))
    RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "500"))
    MAX_WEBSOCKETS_PER_USER: int = int(os.getenv("MAX_WEBSOCKETS_PER_USER", "4"))
    REALTIME_REDIS_URL: str = os.getenv("REALTIME_REDIS_URL", "").strip()
    REALTIME_REDIS_CHANNEL: str = os.getenv(
        "REALTIME_REDIS_CHANNEL",
        "gradebook:realtime",
    ).strip()
    REALTIME_PRESENCE_TTL_SECONDS: int = int(
        os.getenv("REALTIME_PRESENCE_TTL_SECONDS", "90")
    )
    REALTIME_BROKER_REQUIRED: bool = os.getenv(
        "REALTIME_BROKER_REQUIRED",
        "",
    ).lower() in ("1", "true", "yes")
    BACKEND_CORS_ORIGINS: list[str] = _csv_env("BACKEND_CORS_ORIGINS")
    TRUSTED_HOSTS: list[str] = _csv_env("TRUSTED_HOSTS", "*")
    ALLOW_PUBLIC_REGISTRATION: bool = os.getenv(
        "ALLOW_PUBLIC_REGISTRATION", ""
    ).lower() in ("1", "true", "yes")
    ALLOW_WEBSOCKET_QUERY_TOKEN: bool = os.getenv(
        "ALLOW_WEBSOCKET_QUERY_TOKEN", ""
    ).lower() in ("1", "true", "yes")

    # Password recovery email
    SMTP_HOST: str = os.getenv("SMTP_HOST", "").strip()
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "").strip()
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "").strip()
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "GradeBook").strip()
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() in (
        "1", "true", "yes"
    )
    PASSWORD_RESET_URL: str = os.getenv(
        "PASSWORD_RESET_URL", "https://gradebook.su/app/"
    ).strip()
    PASSWORD_RESET_EXPIRE_MINUTES: int = int(
        os.getenv("PASSWORD_RESET_EXPIRE_MINUTES", "30")
    )
    LANDING_LEADS_EMAIL: str = os.getenv(
        "LANDING_LEADS_EMAIL", "applications@gradebook.su"
    ).strip()

    # Manual billing is the launch mode. The optional generic_hmac webhook
    # requires an external payment adapter.
    PAYMENT_PROVIDER: str = os.getenv("PAYMENT_PROVIDER", "none").strip().lower()
    PAYMENT_PROVIDER_PUBLIC_NAME: str = os.getenv(
        "PAYMENT_PROVIDER_PUBLIC_NAME", ""
    ).strip()
    PAYMENT_PROVIDER_ENABLED: bool = os.getenv(
        "PAYMENT_PROVIDER_ENABLED", ""
    ).lower() in ("1", "true", "yes")
    PAYMENT_WEBHOOK_SECRET: str = os.getenv("PAYMENT_WEBHOOK_SECRET", "")
    SUBSCRIPTION_GRACE_DAYS: int = int(os.getenv("SUBSCRIPTION_GRACE_DAYS", "7"))
    SUBSCRIPTION_SYNC_INTERVAL_SECONDS: int = int(
        os.getenv("SUBSCRIPTION_SYNC_INTERVAL_SECONDS", "3600")
    )

    # First Superuser
    FIRST_SUPERUSER: str = os.getenv("FIRST_SUPERUSER") or os.getenv("OWNER_USERNAME", "")
    FIRST_SUPERUSER_PASSWORD: str = os.getenv("FIRST_SUPERUSER_PASSWORD") or os.getenv("OWNER_PASSWORD", "")


settings = Settings()

# Set MYSQL_PASSWORD
# In Docker: docker-compose.yml sets MYSQL_PASSWORD=${MYSQL_ROOT_PASSWORD} for root user
# For local dev: use MYSQL_PASSWORD or fallback to MYSQL_ROOT_PASSWORD if root, else default
_mysql_password = os.getenv("MYSQL_PASSWORD")
_mysql_root_password = os.getenv("MYSQL_ROOT_PASSWORD")
_mysql_user = os.getenv("MYSQL_USER", "root")

if _mysql_password:
    # Docker Compose already sets correct password, or user explicitly set it
    settings.MYSQL_PASSWORD = _mysql_password
elif _mysql_user == "root" and _mysql_root_password:
    # Local dev: connecting as root, use root password
    settings.MYSQL_PASSWORD = _mysql_root_password
else:
    settings.MYSQL_PASSWORD = ""

# Create profile photos directory if it doesn't exist
os.makedirs(settings.PROFILE_PHOTOS_DIR, exist_ok=True)
# Create achievements directory if it doesn't exist
os.makedirs(settings.ACHIEVEMENTS_DIR, exist_ok=True)
# Create homework submissions directory
os.makedirs(settings.HOMEWORK_SUBMISSIONS_DIR, exist_ok=True)
# Create homework attachments directory
os.makedirs(settings.HOMEWORK_ATTACHMENTS_DIR, exist_ok=True)
# Create message attachments directory
os.makedirs(settings.MESSAGE_ATTACHMENTS_DIR, exist_ok=True)
