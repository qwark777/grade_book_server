#!/usr/bin/env python3
"""Generate a production environment file without committing secrets."""

from __future__ import annotations

import argparse
import base64
import secrets
from pathlib import Path


def _password(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _reserved_hostname(value: str) -> bool:
    return value in {
        "localhost",
        "127.0.0.1",
        "example.com",
        "example.net",
        "example.org",
    } or value.endswith(
        (".example", ".example.com", ".example.net", ".example.org", ".invalid")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--domain",
        required=True,
        help="Public frontend hostname, for example school.example.org",
    )
    parser.add_argument(
        "--api-domain",
        help="API hostname; defaults to the frontend hostname",
    )
    parser.add_argument("--output", default=".env.production")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    domain = args.domain.strip().lower()
    api_domain = (args.api_domain or domain).strip().lower()
    if any(
        _reserved_hostname(value)
        or "://" in value
        for value in (domain, api_domain)
    ):
        parser.error("Use real hostnames without http:// or https://")

    output = Path(args.output)
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; use --force to replace it")

    encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    values = {
        "MYSQL_HOST": "db",
        "MYSQL_ROOT_PASSWORD": _password(36),
        "MYSQL_USER": "gradebook_user",
        "MYSQL_PASSWORD": _password(36),
        "MYSQL_DB": "grade_book",
        "MYSQL_PORT": "3306",
        "INIT_DB_ON_STARTUP": "false",
        "APP_HOST": "0.0.0.0",
        "APP_PORT": "8001",
        "BACKEND_CORS_ORIGINS": f"https://{domain}",
        "TRUSTED_HOSTS": f"{domain},{api_domain}",
        "SECRET_KEY": secrets.token_hex(32),
        "ENCRYPTION_KEY": encryption_key,
        "OWNER_USERNAME": "owner",
        "OWNER_PASSWORD": _password(28),
        "OWNER_FULL_NAME": '"System Owner"',
        "MAX_CONCURRENT_REQUESTS": "240",
        "RATE_LIMIT_WINDOW_SECONDS": "15",
        "RATE_LIMIT_MAX_REQUESTS": "500",
        "MAX_WEBSOCKETS_PER_USER": "4",
        "PAYMENT_PROVIDER": "none",
        "PAYMENT_PROVIDER_PUBLIC_NAME": "",
        "PAYMENT_PROVIDER_ENABLED": "false",
        "PAYMENT_WEBHOOK_SECRET": _password(36),
        "SUBSCRIPTION_GRACE_DAYS": "7",
        "SUBSCRIPTION_SYNC_INTERVAL_SECONDS": "3600",
    }
    output.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    output.chmod(0o600)
    print(f"Created {output} with mode 0600")
    print("Store it in a password manager and do not commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
