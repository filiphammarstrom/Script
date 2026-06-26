"""Roller och åtkomstlista (allowlist) för molnläget.

Modell (motsvarar användarens val 1a):
- Admins pekas ut dels via env ADMIN_EMAILS (komma-/semikolon-separerat) som
  failsafe, dels via en lista i data/access.json som admin själv hanterar i
  appen. En admin är alltid också tillåten att logga in.
- När minst en admin finns är åtkomsten BEGRÄNSAD: bara e-postadresser i
  allowlisten (eller admins) får logga in. Saknas admins helt är appen ÖPPEN
  (bakåtkompatibelt) så att ingen kan låsa ute sig själv av misstag.

Allt är ren JSON på disk – ingen databas, i linje med resten av appen.
"""
from __future__ import annotations

import json
import os

from app import store


def _access_path():
    return store.DATA_DIR / "access.json"


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _env_admins() -> set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {_norm(x) for x in raw.replace(";", ",").split(",") if x.strip()}


def _load() -> dict:
    path = _access_path()
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            data = {}
    data.setdefault("allowed_emails", [])
    data.setdefault("admin_emails", [])
    return data


def _save(data: dict) -> None:
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _access_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


# ---- frågor ----
def admin_emails() -> set[str]:
    data = _load()
    return _env_admins() | {_norm(e) for e in data["admin_emails"]}


def allowed_emails() -> set[str]:
    data = _load()
    return admin_emails() | {_norm(e) for e in data["allowed_emails"]}


def access_enabled() -> bool:
    """Begränsningen aktiveras först när minst en admin är konfigurerad."""
    return bool(admin_emails())


def is_admin(email: str) -> bool:
    return _norm(email) in admin_emails()


def is_allowed(email: str) -> bool:
    if not access_enabled():
        return True  # öppet tills första admin pekats ut
    return _norm(email) in allowed_emails()


# ---- admin-hantering ----
def snapshot() -> dict:
    """Vy för admin-sidan: tillåtna (icke-admins), admins och vilka som är låsta via env."""
    data = _load()
    admins = admin_emails()
    allowed_only = {_norm(e) for e in data["allowed_emails"]} - admins
    return {
        "allowed": sorted(allowed_only),
        "admins": sorted(admins),
        "env_admins": sorted(_env_admins()),  # kan inte tas bort i UI:t
    }


def add_allowed(email: str) -> None:
    e = _norm(email)
    if not e:
        return
    data = _load()
    if e not in {_norm(x) for x in data["allowed_emails"]}:
        data["allowed_emails"].append(e)
    _save(data)


def remove_allowed(email: str) -> None:
    e = _norm(email)
    data = _load()
    data["allowed_emails"] = [x for x in data["allowed_emails"] if _norm(x) != e]
    data["admin_emails"] = [x for x in data["admin_emails"] if _norm(x) != e]
    _save(data)


def set_admin(email: str, make_admin: bool) -> None:
    e = _norm(email)
    if not e:
        return
    data = _load()
    current = {_norm(x) for x in data["admin_emails"]}
    if make_admin and e not in current:
        data["admin_emails"].append(e)
    elif not make_admin:
        data["admin_emails"] = [x for x in data["admin_emails"] if _norm(x) != e]
    _save(data)
