import re
from cryptography.fernet import Fernet, InvalidToken
from .config import settings

EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")


def redact_pii(text: str) -> str:
    text = EMAIL.sub("[REDACTED_EMAIL]", text)
    return PHONE.sub("[REDACTED_PHONE]", text)


def encrypt(text: str) -> str:
    if not settings.fernet_key:
        return text
    return Fernet(settings.fernet_key.encode()).encrypt(text.encode()).decode()


def decrypt(text: str) -> str:
    if not settings.fernet_key:
        return text
    try:
        return Fernet(settings.fernet_key.encode()).decrypt(text.encode()).decode()
    except InvalidToken:
        return text
