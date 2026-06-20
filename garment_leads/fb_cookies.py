"""FB session cookie loading, saving, masking, and freshness checks.

Stores cookies at $XDG_CONFIG_HOME/garment-leads/fb_cookies.json (mode 600).
Only stores the fields the scraper needs: c_user, xs, fr, datr, sb, locale,
plus captured_at for freshness tracking.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

REQUIRED_FIELDS: Final[tuple[str, ...]] = ("c_user", "xs")
OPTIONAL_FIELDS: Final[tuple[str, ...]] = ("fr", "datr", "sb", "locale")
ALL_FIELDS: Final[tuple[str, ...]] = REQUIRED_FIELDS + OPTIONAL_FIELDS

C_USER_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9]{10,20}$")

# Default Chromium cookie DB locations to try, in order
COOKIE_DB_CANDIDATES: Final[tuple[Path, ...]] = (
    Path.home() / "snap/chromium/common/chromium/Default/Cookies",
    Path.home() / ".config/chromium/Default/Cookies",
    Path.home() / ".config/google-chrome/Default/Cookies",
    Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies",  # mac
)


@dataclass(frozen=True)
class CookieSnapshot:
    """The 6 fields we store, plus when captured."""

    c_user: str
    xs: str
    fr: str = ""
    datr: str = ""
    sb: str = ""
    locale: str = "en_US"
    captured_at: str = ""

    def to_json_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in ALL_FIELDS if getattr(self, field)} | {
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> CookieSnapshot:
        return cls(
            c_user=str(data.get("c_user", "")),
            xs=str(data.get("xs", "")),
            fr=str(data.get("fr", "")),
            datr=str(data.get("datr", "")),
            sb=str(data.get("sb", "")),
            locale=str(data.get("locale", "en_US")),
            captured_at=str(data.get("captured_at", "")),
        )


def default_cookie_path() -> Path:
    """Return the XDG-resolved cookie file path.

    Resolution: $XDG_CONFIG_HOME/garment-leads/fb_cookies.json,
    falling back to ~/.config/garment-leads/fb_cookies.json.
    """

    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "garment-leads" / "fb_cookies.json"


def mask_c_user(c_user: str) -> str:
    """Mask the middle of a Facebook user ID for safe display.

    Rule: keep first 4 and last 4 chars, replace middle with asterisks.
    For short IDs (≤ 8 chars), mask everything.
    """

    if len(c_user) <= 8:
        return "*" * len(c_user)
    return f"{c_user[:4]}{'*' * (len(c_user) - 8)}{c_user[-4:]}"


def validate(snapshot: CookieSnapshot) -> list[str]:
    """Return a list of validation problems; empty list = valid."""

    problems: list[str] = []
    if not C_USER_RE.match(snapshot.c_user):
        problems.append(f"c_user does not match {C_USER_RE.pattern!r}: {mask_c_user(snapshot.c_user)}")
    if not snapshot.xs:
        problems.append("xs is empty")
    return problems


def freshness_days(snapshot: CookieSnapshot, now: datetime | None = None) -> float | None:
    """Return age in days based on captured_at; None if unparseable."""

    if not snapshot.captured_at:
        return None
    try:
        captured = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    current = now or datetime.now(UTC)
    delta = current - captured
    return delta.total_seconds() / 86400.0


def cookies_are_fresh(
    snapshot: CookieSnapshot,
    max_age_days: int = 30,
    now: datetime | None = None,
) -> bool:
    """True if the cookie snapshot is recent enough to use."""

    age = freshness_days(snapshot, now=now)
    if age is None:
        return False
    return age <= max_age_days


def save_cookies(snapshot: CookieSnapshot, path: Path | None = None) -> Path:
    """Persist cookies to disk atomically with mode 0600.

    Returns the resolved path used.
    """

    target = path or default_cookie_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_json_dict()
    fd, tmp_name = tempfile.mkstemp(prefix=".fb_cookies.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return target


def load_cookies(path: Path | None = None, max_age_days: int = 30) -> CookieSnapshot | None:
    """Load cookies from disk if they exist, are valid, and are fresh enough.

    Returns None on any problem (missing file, invalid JSON, schema mismatch,
    validation failure, staleness). Caller logs the reason separately.
    """

    target = path or default_cookie_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    snapshot = CookieSnapshot.from_json_dict(data)
    if validate(snapshot):
        return None
    if not cookies_are_fresh(snapshot, max_age_days=max_age_days):
        return None
    return snapshot


# ---------- Chromium cookie DB reading (for capture-cookies command) ----------

# Linux Chromium password key is "peanuts" (legacy v10) or "Chrome Safe Storage"
# (v11 / current). Snap Chromium uses the snap keyring interface.
LINUX_CHROME_KEY: Final[bytes] = b"peanuts"


def _decrypt_linux_cookie(encrypted_value: bytes, key: bytes) -> bytes | None:
    """Decrypt a Chromium cookie value on Linux.

    Chromium v10 uses a fixed-key DPAPI-like scheme (prefix 'v10' then AES-CBC).
    Returns None on any decryption error.
    """

    try:
        from Crypto.Cipher import AES  # type: ignore[import-untyped]
        from Crypto.Protocol.KDF import scrypt  # type: ignore[import-untyped]
    except ImportError:
        return None
    if not encrypted_value:
        return b""
    if not encrypted_value.startswith(b"v10"):
        # Plaintext (older Chromium or not encrypted)
        try:
            return encrypted_value.decode("utf-8").encode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        # v10 layout: 3 bytes prefix 'v10' + 12 bytes nonce + at least 16 bytes ciphertext
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        derived = scrypt(key, salt=b"saltysalt", key_len=16, N=1024, r=8, p=1)
        cipher = AES.new(derived, AES.MODE_CBC, IV=nonce)
        decrypted = cipher.decrypt(ciphertext)
        # PKCS7 padding strip
        pad_len = decrypted[-1]
        if pad_len < 1 or pad_len > 16:
            return None
        return decrypted[:-pad_len]
    except Exception:
        return None


def _read_chromium_cookies_raw(db_path: Path) -> list[tuple[str, str]]:
    """Read .facebook.com cookies from a Chromium Cookies SQLite DB.

    Returns (name, encrypted_value_bytes) pairs WITHOUT attempting decryption
    (Linux keyring is needed). Caller is responsible for decryption.
    """

    if not db_path.exists():
        return []
    # Chromium locks the DB; copy to a temp file first
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        tmp_path.write_bytes(db_path.read_bytes())
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            rows = conn.execute(
                "SELECT name, value, host_key FROM cookies WHERE host_key LIKE '%facebook.com'"
            ).fetchall()
            return [(name, value) for name, value, _ in rows]
        finally:
            conn.close()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def capture_from_chromium(
    db_path: Path | None = None,
    decrypt_key: bytes | None = None,
    locale: str = "en_US",
) -> CookieSnapshot | None:
    """Capture FB session cookies from a Chromium Cookies SQLite DB.

    On Linux, decrypt_key defaults to LINUX_CHROME_KEY (the legacy Chromium key).
    Returns a CookieSnapshot with the required fields, or None if c_user/xs not found.
    """

    candidate = db_path or next((p for p in COOKIE_DB_CANDIDATES if p.exists()), None)
    if candidate is None:
        return None
    raw = _read_chromium_cookies_raw(candidate)
    if not raw:
        return None

    key = decrypt_key or LINUX_CHROME_KEY
    decoded: dict[str, str] = {}
    for name, encrypted_value in raw:
        # sqlite3 may return str or bytes depending on detect_types; coerce
        if isinstance(encrypted_value, str):
            encrypted_value_bytes = encrypted_value.encode("latin-1")
        else:
            encrypted_value_bytes = encrypted_value
        plaintext = _decrypt_linux_cookie(encrypted_value_bytes, key)
        if plaintext is None:
            continue
        try:
            decoded[name] = plaintext.decode("utf-8")
        except UnicodeDecodeError:
            continue

    c_user = decoded.get("c_user", "")
    xs = decoded.get("xs", "")
    if not c_user or not xs:
        return None
    return CookieSnapshot(
        c_user=c_user,
        xs=xs,
        fr=decoded.get("fr", ""),
        datr=decoded.get("datr", ""),
        sb=decoded.get("sb", ""),
        locale=locale,
        captured_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )