import json
import base64
import hmac
import hashlib
import warnings
from datetime import datetime, timedelta, UTC
from typing import Dict

from passlib.context import CryptContext
from cryptography.fernet import Fernet
from fastapi import HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

# Suppress bcrypt version warning from passlib
warnings.filterwarnings('ignore', message='.*bcrypt.*', category=UserWarning)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Encryption
fernet = Fernet(settings.ENCRYPTION_KEY.encode())


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash password"""
    return pwd_context.hash(password)


def encrypt_message(message: str) -> str:
    """Encrypt message"""
    return fernet.encrypt(message.encode()).decode()


def decrypt_message(encrypted_message: str) -> str:
    """Decrypt message"""
    return fernet.decrypt(encrypted_message.encode()).decode()


def create_access_token(data: Dict) -> str:
    """Create JWT access token"""
    header = {"alg": "HS256", "typ": "JWT"}
    header_encoded = base64.urlsafe_b64encode(
        json.dumps(header).encode()
    ).decode().rstrip("=")

    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    data = dict(data)
    data["exp"] = expire.timestamp()
    payload_encoded = base64.urlsafe_b64encode(
        json.dumps(data).encode()
    ).decode().rstrip("=")

    message = f"{header_encoded}.{payload_encoded}"
    signature = hmac.new(
        settings.SECRET_KEY.encode(), 
        message.encode(), 
        hashlib.sha256
    )
    signature_encoded = base64.urlsafe_b64encode(
        signature.digest()
    ).decode().rstrip("=")
    
    return f"{header_encoded}.{payload_encoded}.{signature_encoded}"


def verify_token(token: str) -> Dict:
    """Verify JWT token"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError("Invalid token format")

        header_encoded, payload_encoded, signature_encoded = parts

        def add_padding(data: str) -> str:
            return data + '=' * (-len(data) % 4)

        signature = base64.urlsafe_b64decode(add_padding(signature_encoded))
        message = f"{header_encoded}.{payload_encoded}"
        expected_signature = hmac.new(
            settings.SECRET_KEY.encode(), 
            message.encode(), 
            hashlib.sha256
        ).digest()

        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Invalid signature")

        payload = base64.urlsafe_b64decode(add_padding(payload_encoded)).decode()
        payload_data = json.loads(payload)

        if "exp" not in payload_data:
            raise ValueError("Token expiration missing")
        if datetime.now(UTC).timestamp() > payload_data["exp"]:
            raise ValueError("Token expired")

        return payload_data
    except Exception as e:
        raise ValueError(f"Token verification failed: {str(e)}")


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Get current authenticated user"""
    from app.db.user_operations import get_user
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = verify_token(token)
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    
    user = await get_user(username)
    if user is None:
        raise credentials_exception
    
    return user


