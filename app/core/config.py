import os
from typing import Optional


class Settings:
    # JWT Configuration
    SECRET_KEY: str = "304f1388f317fe2e917a1df468144def7f60586ba96dc80b07d26c68cae00fab"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 604800  # 7 days
    
    # Encryption
    ENCRYPTION_KEY: str = "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k="
    
    # Database Configuration
    # Default to 'db' for Docker, override with MYSQL_HOST env var for local development
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "db")
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    # MYSQL_PASSWORD will be set after class definition
    MYSQL_DB: str = os.getenv("MYSQL_DB", "grade_book")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    
    # File Storage
    PROFILE_PHOTOS_DIR: str = "profile_photos"
    ACHIEVEMENTS_DIR: str = "achievements"
    
    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Grade Book API"


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
    # Default fallback
    settings.MYSQL_PASSWORD = "12345678"

# Create profile photos directory if it doesn't exist
os.makedirs(settings.PROFILE_PHOTOS_DIR, exist_ok=True)
# Create achievements directory if it doesn't exist
os.makedirs(settings.ACHIEVEMENTS_DIR, exist_ok=True)


