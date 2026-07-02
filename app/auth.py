"""Inloggning: Google Identity Services (ID-token) + signerad session.

Designval:
- AUTH_ENABLED=false (default): lokalt enanvändarläge – ingen inloggning, allt sker
  som användaren "local". Din dubbelklicks-app fungerar precis som förr.
- AUTH_ENABLED=true (på servern): Google-login krävs; varje konto får sin egen data.

Vi använder Googles ID-token-flöde (frontend-knappen "Sign in with Google") som bara
kräver ett publikt GOOGLE_CLIENT_ID – inget client secret. ID-token verifieras hos
Google via tokeninfo-endpointen och vi kontrollerar att 'aud' matchar vårt client-ID.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from fastapi import HTTPException, Request

from app import store

_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_VALID_ISS = {"accounts.google.com", "https://accounts.google.com"}


def auth_enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "false").lower() in ("1", "true", "yes")


def google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def verify_google_id_token(token: str) -> dict:
    """Verifiera ett Google ID-token och returnera {sub, email, name}.

    Höjer ValueError om token är ogiltigt eller utfärdat för fel app.
    """
    client_id = google_client_id()
    if not client_id:
        raise ValueError("GOOGLE_CLIENT_ID är inte satt på servern.")
    url = _TOKENINFO_URL + "?" + urllib.parse.urlencode({"id_token": token})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # nätverksfel eller ogiltigt token (4xx)
        raise ValueError(f"Kunde inte verifiera Google-token: {exc}")
    if data.get("aud") != client_id:
        raise ValueError("Token utfärdat för en annan app (aud matchar inte).")
    if data.get("iss") not in _VALID_ISS:
        raise ValueError("Ogiltig utfärdare (iss).")
    if not data.get("sub"):
        raise ValueError("Token saknar sub.")
    # Utan denna koll kan vem som helst registrera ett Google-konto med någon
    # ANNANS (overifierade) e-postadress och bli insläppt/admin via e-postmatchningen
    # i allowlisten – e-posten får bara litas på när Google intygat att den är ägd.
    if data.get("email") and data.get("email_verified") not in (True, "true"):
        raise ValueError("E-postadressen i kontot är inte verifierad hos Google.")
    return {
        "sub": data["sub"],
        "email": data.get("email", ""),
        "name": data.get("name", ""),
    }


def current_uid(request: Request) -> str:
    """FastAPI-beroende: returnerar inloggad användares uid, annars 401.

    I lokalt läge (auth av) returneras alltid 'local'.
    """
    if not auth_enabled():
        return store.LOCAL_UID
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Inte inloggad")
    return uid
