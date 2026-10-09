import base64
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = ("SECRET_KEY", "ENCRYPTION_KEY")
REQUIRED_DIRS = (
    "profile_photos",
    "achievements",
    "homework_submissions",
    "homework_attachments",
    "message_attachments",
)
PLACEHOLDER_VALUES = {
    "change_me_to_a_long_random_value",
    "replace_with_long_random_root_password",
    "replace_with_long_random_database_password",
    "replace_with_openssl_rand_hex_32",
    "replace_with_python_fernet_key",
    "replace_with_long_random_owner_password",
    "replace_with_provider_webhook_secret",
    "generate_with_openssl_rand_hex_32",
    "generate_with_python_cryptography_fernet_key",
}
RESERVED_DOMAIN_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".invalid",
)


def _ok(message: str) -> None:
    print(f"OK   {message}")


def _fail(message: str) -> None:
    print(f"FAIL {message}")


def _warn(message: str) -> None:
    print(f"WARN {message}")


def _is_fernet_key(value: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(value.encode())
    except Exception:
        return False
    return len(decoded) == 32


def main() -> int:
    failures = 0
    strict_production = os.getenv("GRADEBOOK_ENV", "").lower() in {
        "prod",
        "production",
    } or os.getenv("STRICT_PRODUCTION_CHECKS") == "1"

    def production_problem(message: str) -> None:
        nonlocal failures
        if strict_production:
            _fail(message)
            failures += 1
        else:
            _warn(message)

    for name in REQUIRED_ENV:
        value = os.getenv(name, "")
        if len(value) < 32:
            _fail(f"{name} is missing or too short")
            failures += 1
        elif value in PLACEHOLDER_VALUES:
            _fail(f"{name} still contains a placeholder value")
            failures += 1
        else:
            _ok(f"{name} is set")

    encryption_key = os.getenv("ENCRYPTION_KEY", "")
    if encryption_key and not _is_fernet_key(encryption_key):
        _fail("ENCRYPTION_KEY is not a valid Fernet key")
        failures += 1

    if os.getenv("ALLOW_INSECURE_DEV_DEFAULTS"):
        production_problem(
            "ALLOW_INSECURE_DEV_DEFAULTS is enabled; do not use this in production"
        )

    if os.getenv("MYSQL_USER", "root") == "root":
        production_problem(
            "MYSQL_USER=root; prefer a dedicated database user in production"
        )

    trusted_hosts = [item.strip() for item in os.getenv("TRUSTED_HOSTS", "*").split(",") if item.strip()]
    if "*" in trusted_hosts:
        production_problem(
            "TRUSTED_HOSTS allows any host; set explicit API hostnames in production"
        )

    if not os.getenv("BACKEND_CORS_ORIGINS"):
        production_problem(
            "BACKEND_CORS_ORIGINS is not set; local development origins will be used"
        )
    elif strict_production:
        for raw_origin in os.getenv("BACKEND_CORS_ORIGINS", "").split(","):
            origin = raw_origin.strip()
            parsed = urlparse(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.hostname in {"example.com", "example.net", "example.org"}
                or parsed.hostname.endswith(RESERVED_DOMAIN_SUFFIXES)
                or parsed.hostname in {"localhost", "127.0.0.1"}
            ):
                _fail(f"BACKEND_CORS_ORIGINS contains non-production origin: {origin}")
                failures += 1

    if strict_production:
        forbidden_hosts = {"*", "localhost", "127.0.0.1"}
        for host in trusted_hosts:
            if (
                host in forbidden_hosts
                or host in {"example.com", "example.net", "example.org"}
                or host.endswith(RESERVED_DOMAIN_SUFFIXES)
            ):
                _fail(f"TRUSTED_HOSTS contains non-production hostname: {host}")
                failures += 1

    payment_enabled = os.getenv("PAYMENT_PROVIDER_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
    }
    payment_provider = os.getenv("PAYMENT_PROVIDER", "none").strip().lower()
    if payment_enabled and payment_provider in {"", "none"}:
        _fail("PAYMENT_PROVIDER_ENABLED=true but PAYMENT_PROVIDER is not configured")
        failures += 1
    if strict_production and payment_provider != "none":
        webhook_secret = os.getenv("PAYMENT_WEBHOOK_SECRET", "")
        if len(webhook_secret) < 24 or webhook_secret in PLACEHOLDER_VALUES:
            _fail("PAYMENT_WEBHOOK_SECRET is missing, too short, or still a placeholder")
            failures += 1
    if payment_enabled and payment_provider != "generic_hmac":
        _fail(
            "Only PAYMENT_PROVIDER=generic_hmac is supported when payments are enabled"
        )
        failures += 1

    if strict_production and os.getenv(
        "INIT_DB_ON_STARTUP", ""
    ).lower() in {"1", "true", "yes"}:
        _fail(
            "INIT_DB_ON_STARTUP must be false in production; run Alembic before startup"
        )
        failures += 1

    for dirname in REQUIRED_DIRS:
        path = ROOT / dirname
        path.mkdir(parents=True, exist_ok=True)
        if os.access(path, os.W_OK):
            _ok(f"{dirname}/ is writable")
        else:
            _fail(f"{dirname}/ is not writable")
            failures += 1

    owner_password = os.getenv("OWNER_PASSWORD", "")
    if not owner_password:
        _warn("OWNER_PASSWORD is not set; first owner will not be auto-created")
    elif owner_password in PLACEHOLDER_VALUES:
        _fail("OWNER_PASSWORD still contains a placeholder value")
        failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
