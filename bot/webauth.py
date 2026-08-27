"""Authentication for the web interface.

The whole site is private: every route is gated by ``auth_guard`` in bot.web, and
an unapproved visitor only ever reaches the login page. There is no public
sign-up — an admin creates accounts on the /admin/users page.

Security choices (all stdlib, no extra deps):
- Passwords: salted scrypt, per-user 16-byte salt, constant-time verify.
- Sessions: a stateless HMAC-signed cookie (uid + expiry), so a tampered or
  expired cookie is rejected; the signing key is SESSION_SECRET.
- CSRF: state-changing forms carry an HMAC token bound to the session cookie.
- Login throttle: per-IP lockout after repeated failures.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from bot.log import get_logger

log = get_logger("webauth")

SESSION_COOKIE = "tb_auth"
SESSION_TTL = 30 * 86400          # 30 days
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024

# login throttle: after _LOCK_MAX failures within _LOCK_WINDOW seconds from one
# IP, reject for the rest of the window
_LOCK_MAX = 8
_LOCK_WINDOW = 900
_fails: dict[str, list] = {}      # ip -> [count, window_start_ts]

_secret_cache: bytes | None = None


# --------------------------------------------------------------------------- #
# secret / signing key
# --------------------------------------------------------------------------- #
def _secret() -> bytes:
    """32-byte signing key derived from SESSION_SECRET (falls back to WEB_TOKEN).
    If neither is set we generate an ephemeral key and warn — logins then don't
    survive a restart, which is fine for local dev but must be fixed in prod."""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    raw = os.environ.get("SESSION_SECRET") or os.environ.get("WEB_TOKEN")
    if not raw:
        log.warning("SESSION_SECRET unset — using an ephemeral key; sessions will "
                    "not survive a restart. Set SESSION_SECRET in production.")
        raw = base64.urlsafe_b64encode(os.urandom(32)).decode()
    _secret_cache = hashlib.sha256(raw.encode()).digest()
    return _secret_cache


# --------------------------------------------------------------------------- #
# password hashing
# --------------------------------------------------------------------------- #
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                        p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM, dklen=32)
    return f"scrypt${_SCRYPT_N}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        want = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=int(n),
                            r=_SCRYPT_R, p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM,
                            dklen=len(want))
        return hmac.compare_digest(dk, want)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# session tokens (stateless, signed)
# --------------------------------------------------------------------------- #
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session_token(uid: int, ttl: int = SESSION_TTL) -> str:
    payload = f"{uid}.{int(time.time()) + ttl}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def parse_session_token(tok: str) -> int | None:
    try:
        uid_s, exp_s, sig_b64 = tok.split(".")
        payload = f"{uid_s}.{exp_s}"
        want = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(sig_b64)):
            return None
        if int(exp_s) < time.time():
            return None
        return int(uid_s)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# CSRF — token bound to the caller's session cookie
# --------------------------------------------------------------------------- #
def csrf_token(session_cookie: str) -> str:
    return _b64(hmac.new(_secret(), (session_cookie + ":csrf").encode(),
                         hashlib.sha256).digest())


def csrf_ok(session_cookie: str | None, supplied: str | None) -> bool:
    if not session_cookie or not supplied:
        return False
    return hmac.compare_digest(csrf_token(session_cookie), supplied)


# --------------------------------------------------------------------------- #
# request helpers
# --------------------------------------------------------------------------- #
def current_user(request, db):
    """The AppUser for this request's session cookie, or None. Rejects unknown or
    deactivated users so revoking access is immediate."""
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    uid = parse_session_token(tok)
    if uid is None:
        return None
    from bot.models import AppUser
    u = db.get(AppUser, uid)
    if u is None or not u.is_active:
        return None
    return u


# --------------------------------------------------------------------------- #
# login throttle
# --------------------------------------------------------------------------- #
def throttled(ip: str) -> bool:
    rec = _fails.get(ip)
    if not rec:
        return False
    count, start = rec
    if time.time() - start > _LOCK_WINDOW:
        _fails.pop(ip, None)
        return False
    return count >= _LOCK_MAX


def record_failure(ip: str) -> None:
    now = time.time()
    rec = _fails.get(ip)
    if not rec or now - rec[1] > _LOCK_WINDOW:
        _fails[ip] = [1, now]
    else:
        rec[0] += 1


def clear_failures(ip: str) -> None:
    _fails.pop(ip, None)


# --------------------------------------------------------------------------- #
# bootstrap the first admin from env (idempotent, run on web startup)
# --------------------------------------------------------------------------- #
def bootstrap_admin(db) -> None:
    from sqlalchemy import func, select

    from bot.models import AppUser
    if db.execute(select(func.count(AppUser.id))).scalar():
        return  # users already exist — never auto-create after that
    user = (os.environ.get("ADMIN_USERNAME") or "").strip().lower()
    pw = os.environ.get("ADMIN_PASSWORD") or ""
    if not user or not pw:
        log.warning("no app users yet and ADMIN_USERNAME/ADMIN_PASSWORD unset — "
                    "set both to bootstrap the first admin, then remove them")
        return
    db.add(AppUser(username=user, password_hash=hash_password(pw),
                   is_admin=True, is_active=True, created_by="bootstrap"))
    db.commit()
    log.info("bootstrapped first admin", username=user)


def normalize_username(name: str) -> str:
    return (name or "").strip().lower()

# --------------------------------------------------------------------------- #
# single-owner access key (replaces the username/password login)
# --------------------------------------------------------------------------- #
ACCESS_COOKIE = "tb_key"


def access_key() -> str:
    """The shared secret that admits the owner. ACCESS_KEY, falling back to the
    pre-existing WEB_TOKEN so a deploy that already sets it keeps working.

    Empty means NO key is configured. Callers must treat that as 'refuse
    everyone' rather than 'let everyone in' — an unset env var must never be the
    thing that opens a page showing a real Kalshi balance to the internet."""
    return (os.environ.get("ACCESS_KEY") or os.environ.get("WEB_TOKEN") or "").strip()


def access_key_ok(supplied: str | None) -> bool:
    """Constant-time compare. False whenever no key is configured."""
    key = access_key()
    if not key or not supplied:
        return False
    return hmac.compare_digest(supplied.strip(), key)


def owner_user(db):
    """The single account everything is attributed to.

    Prefers the lowest-id admin, then the lowest-id active user; creates one if
    the table is empty so a fresh database works without a bootstrap password.
    A real AppUser row must exist because user_bets.user_id and friends are
    foreign keys into it."""
    from sqlalchemy import select

    from bot.models import AppUser
    u = db.execute(select(AppUser).where(AppUser.is_active.is_(True),
                                         AppUser.is_admin.is_(True))
                   .order_by(AppUser.id)).scalars().first()
    if u is None:
        u = db.execute(select(AppUser).where(AppUser.is_active.is_(True))
                       .order_by(AppUser.id)).scalars().first()
    if u is None:
        u = AppUser(username=(os.environ.get("OWNER_USERNAME") or "owner").strip().lower(),
                    password_hash="", is_admin=True, is_active=True,
                    created_by="access-key")
        db.add(u)
        db.commit()
        log.info("created the owner account", username=u.username)
    return u
