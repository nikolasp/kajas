"""Tests for the auth module."""

from __future__ import annotations

from kajas import auth


def test_hash_and_verify_passphrase() -> None:
    h = auth.hash_passphrase("hunter2")
    assert auth.verify_passphrase(h, "hunter2") is True
    assert auth.verify_passphrase(h, "wrong") is False


def test_session_roundtrip() -> None:
    secret = auth.generate_session_secret()
    token = auth.issue_session(secret)
    user = auth.read_session(secret, token)
    assert user is not None
    assert user.name == "admin"


def test_session_tamper_is_rejected() -> None:
    secret = auth.generate_session_secret()
    token = auth.issue_session(secret)
    bad = token[:-2] + "ab"
    assert auth.read_session(secret, bad) is None


def test_session_missing_is_none() -> None:
    assert auth.read_session("any", None) is None
    assert auth.read_session("any", "") is None
