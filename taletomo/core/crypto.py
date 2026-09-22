import base64
import hashlib
import os
import re
from typing import Dict, Tuple
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


class SecretBox:
    """Provides authenticated symmetric encryption (Fernet/AES-128-CBC + HMAC-SHA256)

    Supports key versioning and key rotation for encrypted credentials at rest.
    """

    def __init__(self, key_ring: Dict[str, str] = None):
        """Initialize with key ring: dict of version -> 32-byte urlsafe base64 encoded key."""
        if not key_ring:
            base_key = getattr(settings, "TALETOMO_ENCRYPTION_KEY", "bm92ZWwtbWFrZXItc3VwZXItc2VjcmV0LWtleS0xMjM0NTY=")
            # Ensure 32 bytes urlsafe base64
            derived_32 = hashlib.sha256(base_key.encode("utf-8")).digest()
            fernet_key = base64.urlsafe_b64encode(derived_32).decode("utf-8")
            self._key_ring = {"v1": fernet_key}
        else:
            self._key_ring = key_ring

        self._fernets: Dict[str, Fernet] = {
            ver: Fernet(k.encode("utf-8")) for ver, k in self._key_ring.items()
        }

    def encrypt(self, plaintext: str, key_version: str = "v1") -> Tuple[str, str]:
        """Encrypt plaintext string. Returns (ciphertext, key_version)."""
        if not plaintext:
            return "", key_version
        if key_version not in self._fernets:
            raise ValueError(f"Unknown encryption key version: {key_version}")

        fernet = self._fernets[key_version]
        token = fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8"), key_version

    def decrypt(self, ciphertext: str, key_version: str = "v1") -> str:
        """Decrypt ciphertext token using specified or available key versions."""
        if not ciphertext:
            return ""
        if key_version in self._fernets:
            fernet = self._fernets[key_version]
            return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

        # Fallback to MultiFernet across all available keys
        fernets = list(self._fernets.values())
        multi = MultiFernet(fernets)
        return multi.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def rotate(self, ciphertext: str, old_version: str, new_version: str) -> Tuple[str, str]:
        """Re-encrypt ciphertext under new key version."""
        plaintext = self.decrypt(ciphertext, old_version)
        return self.encrypt(plaintext, new_version)


_default_secret_box = None


def get_secret_box() -> SecretBox:
    global _default_secret_box
    if _default_secret_box is None:
        _default_secret_box = SecretBox()
    return _default_secret_box


def mask_secret(secret: str) -> str:
    """Mask secret key for safe UI display (e.g. sk-...xyz9)."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "••••••••"
    prefix = secret[:4]
    suffix = secret[-4:]
    return f"{prefix}••••••••{suffix}"


def redact_secrets(text: str, known_secrets: list = None) -> str:
    """Redact sensitive keys/tokens from logs and messages."""
    if not text:
        return ""
    output = text
    if known_secrets:
        for sec in known_secrets:
            if sec and len(sec) >= 6 and sec in output:
                output = output.replace(sec, "[REDACTED_SECRET]")

    # Common API key patterns
    output = re.sub(r"sk-[a-zA-Z0-9_\-]{20,}", "[REDACTED_API_KEY]", output)
    output = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED_BEARER]", output)
    return output
