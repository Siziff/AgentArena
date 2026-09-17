"""Treasure generation, validation, and verification digests.

A treasure is a unique random string of a fixed length (default 128 chars)
from a defined alphabet. The Judge never stores the plaintext treasure; it
stores a SHA-256 digest and compares digests in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

DEFAULT_LENGTH = 128
DEFAULT_ALPHABET = string.hexdigits.lower()[:16]  # "0123456789abcdef"


def generate_treasure(length: int = DEFAULT_LENGTH, alphabet: str = DEFAULT_ALPHABET) -> str:
    """Return a cryptographically-random treasure string.

    Args:
        length: number of characters (default 128).
        alphabet: allowed characters (default lowercase hex).

    Raises:
        ValueError: if length < 1 or alphabet has fewer than 2 symbols.
    """
    if length < 1:
        raise ValueError("treasure length must be >= 1")
    if len(set(alphabet)) < 2:
        raise ValueError("alphabet must contain at least 2 distinct symbols")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def validate_treasure(
    value: str, length: int = DEFAULT_LENGTH, alphabet: str = DEFAULT_ALPHABET
) -> bool:
    """Return True if `value` is a well-formed treasure string.

    A guess that fails validation is rejected by the Judge with HTTP 400
    (it is not evaluated against the secret and does not consume the budget).
    """
    if not isinstance(value, str) or len(value) != length:
        return False
    allowed = set(alphabet)
    return all(ch in allowed for ch in value)


def treasure_digest(value: str) -> str:
    """Return the SHA-256 hex digest used to verify a treasure without storing it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digests_equal(guess: str, known_digest: str) -> bool:
    """Constant-time comparison of a guess against a stored digest."""
    return hmac.compare_digest(treasure_digest(guess), known_digest)
