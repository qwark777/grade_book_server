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
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "12345678")
    MYSQL_DB: str = os.getenv("MYSQL_DB", "grade_book")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    
    # File Storage
    PROFILE_PHOTOS_DIR: str = "profile_photos"
    ACHIEVEMENTS_DIR: str = "achievements"
    
    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Grade Book API"


settings = Settings()

# Create profile photos directory if it doesn't exist
os.makedirs(settings.PROFILE_PHOTOS_DIR, exist_ok=True)
# Create achievements directory if it doesn't exist
os.makedirs(settings.ACHIEVEMENTS_DIR, exist_ok=True)


