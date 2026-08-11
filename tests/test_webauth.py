"""bot.webauth — password hashing, signed sessions, CSRF, login throttle."""
import os

os.environ.setdefault("SESSION_SECRET", "test-secret-for-webauth")

from bot import webauth  # noqa: E402


def test_password_hash_roundtrip_and_reject():
    h = webauth.hash_password("correct horse battery staple")
    assert h.startswith("scrypt$")
    assert webauth.verify_password("correct horse battery staple", h)
    assert not webauth.verify_password("wrong password", h)
    # a second hash of the same password uses a fresh salt → different digest
    assert webauth.hash_password("x") != webauth.hash_password("x")


def test_verify_rejects_garbage():
    assert not webauth.verify_password("x", "not-a-valid-hash")
    assert not webauth.verify_password("x", "")


def test_session_token_roundtrip_and_tamper():
    tok = webauth.make_session_token(42)
    assert webauth.parse_session_token(tok) == 42
    # tampered signature rejected
    assert webauth.parse_session_token(tok[:-2] + "xx") is None
    # a different uid in the payload invalidates the signature
    uid_s, exp_s, sig = tok.split(".")
    assert webauth.parse_session_token(f"99.{exp_s}.{sig}") is None
    # garbage rejected
    assert webauth.parse_session_token("nonsense") is None


def test_session_token_expiry():
    expired = webauth.make_session_token(7, ttl=-10)
    assert webauth.parse_session_token(expired) is None


def test_csrf_bound_to_session():
    a = webauth.csrf_token("session-cookie-A")
    assert webauth.csrf_ok("session-cookie-A", a)
    assert not webauth.csrf_ok("session-cookie-B", a)   # different session
    assert not webauth.csrf_ok("session-cookie-A", "")  # empty
    assert not webauth.csrf_ok(None, a)


def test_login_throttle():
    ip = "203.0.113.7"
    webauth.clear_failures(ip)
    assert not webauth.throttled(ip)
    for _ in range(webauth._LOCK_MAX):
        webauth.record_failure(ip)
    assert webauth.throttled(ip)
    webauth.clear_failures(ip)
    assert not webauth.throttled(ip)
