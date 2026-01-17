from cryptography.fernet import Fernet
import os
import base64
from functools import lru_cache
from dotenv import load_dotenv

# Load .env explicitly to ensure we get the key even if server wasn't restarted
load_dotenv()

# Generate a key if it doesn't exist, but typically we want to load from env
# If BOT_TOKEN_KEY is not in env, we can default to a derived key from SECRET_KEY or generate a new one (but that invalidates on restart)
# So we must enforce it in env.

def get_encryption_key() -> bytes:
    key = os.getenv("BOT_TOKEN_KEY")
    if not key:
        # Try reloading .env one more time
        load_dotenv(override=True)
        key = os.getenv("BOT_TOKEN_KEY")
        
    if not key:
        # Fallback or error? For now, let's auto-generate and warn, 
        # but auto-generating means data loss on restart if not persisted.
        # Better to derive from a static secret if available, or raise error.
        # But for smooth dev experience, let's try to look for a key file.
        raise ValueError("BOT_TOKEN_KEY must be set in environment variables")
    return key.encode() if isinstance(key, str) else key

@lru_cache()
def get_cipher_suite():
    key = get_encryption_key()
    return Fernet(key)

def encrypt_token(token: str) -> str:
    """Encrypts a token string."""
    if not token:
        return ""
    cipher_suite = get_cipher_suite()
    encrypted_bytes = cipher_suite.encrypt(token.encode())
    return encrypted_bytes.decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypts an encrypted token string."""
    if not encrypted_token:
        return ""
    try:
        cipher_suite = get_cipher_suite()
        decrypted_bytes = cipher_suite.decrypt(encrypted_token.encode())
        return decrypted_bytes.decode()
    except Exception as e:
        # Fallback: maybe it wasn't encrypted? (Legacy data support)
        # Check if it looks like a valid token (numbers:chars)
        if ":" in encrypted_token and len(encrypted_token.split(":")[0]) > 5:
             return encrypted_token
        raise e

def generate_key() -> str:
    """Generates a new Fernet key."""
    return Fernet.generate_key().decode()
