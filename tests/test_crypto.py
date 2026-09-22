import pytest
from taletomo.core.crypto import SecretBox, mask_secret, redact_secrets


def test_secretbox_encrypt_decrypt():
    box = SecretBox()
    raw_key = "sk-proj-test1234567890abcdefghijklmnopqrstuvwxyz"
    encrypted, ver = box.encrypt(raw_key, key_version="v1")

    assert encrypted != raw_key
    assert ver == "v1"

    decrypted = box.decrypt(encrypted, key_version="v1")
    assert decrypted == raw_key


def test_mask_secret():
    assert mask_secret("sk-1234567890abcdef") == "sk-1••••••••cdef"
    assert mask_secret("short") == "••••••••"
    assert mask_secret("") == ""


def test_redact_secrets():
    raw_key = "sk-proj-test1234567890abcdefghijkl"
    log_text = f"Connection failed with key {raw_key} at endpoint."
    redacted = redact_secrets(log_text, known_secrets=[raw_key])

    assert raw_key not in redacted
    assert "[REDACTED" in redacted
