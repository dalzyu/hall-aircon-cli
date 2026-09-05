#!/usr/bin/env python3
"""hall_aircon_api.py — shared API client for the Hall Aircon CLI and GUI.

Talks to the same public HTTPS API the official app uses, with your own
account. Standard library only.
"""

import json
import os
import tempfile
import urllib.error
import urllib.request

BASE_URL = os.environ.get("HALL_AIRCON_API", "https://apintu-prod.daikinpayu.com").rstrip("/")
SAML_PREFIX = "https://cmsntu-prod.daikinpayu.com/adfs/saml/redirect/"
CONFIG_PATH = os.path.join(
    os.environ.get("HALL_AIRCON_CONFIG_DIR", os.path.expanduser("~/.config/hall-aircon")),
    "config.json",
)
# Same User-Agent the official app sends; the API edge blocks generic Python
# clients otherwise.
USER_AGENT = os.environ.get("HALL_AIRCON_USER_AGENT", "Dart/3.0 (dart:io)")

FAN_LEVELS = ("A", "L", "LM", "M", "MH", "H")


class ApiError(Exception):
    """An error reported by the API (or the network layer)."""

    def __init__(self, status: int, message: str):
        super().__init__(message or f"HTTP {status}")
        self.status = status


def api_request(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    """Perform an API call and return the JSON envelope, or raise ApiError."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw) if raw.strip() else {}
            if not isinstance(result, dict):
                raise ApiError(0, "invalid API response: expected an object")
            meta = result.get("meta") or {}
            if not isinstance(meta, dict):
                raise ApiError(0, "invalid API response: expected metadata object")
            status = meta.get("status", 200)
            if status != 200:
                raise ApiError(status, meta.get("message") or "API request failed")
            return result
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        message = raw.strip() or e.reason
        try:
            error = json.loads(raw)
            if isinstance(error, dict) and isinstance(error.get("meta"), dict):
                message = error["meta"].get("message") or message
        except json.JSONDecodeError:
            pass
        raise ApiError(e.code, message) from None
    except urllib.error.URLError as e:
        raise ApiError(0, f"network error: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise ApiError(0, f"network error: {e}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(0, "invalid API response: expected UTF-8 JSON") from None


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config if isinstance(config, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    # mkstemp creates a private file on POSIX; replace only after a full write.
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(temporary, CONFIG_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_token() -> str | None:
    return os.environ.get("HALL_AIRCON_TOKEN") or load_config().get("token")


def clear_token() -> None:
    config = load_config()
    config.pop("token", None)
    save_config(config)


def login(email: str, password: str | None = None, fcm_token: str = "",
          get_redirect=None, get_password=None) -> str:
    """Log in and store the session token.

    For student accounts, get_redirect(login_url) must return the full final
    redirect URL after the user completes NTU SSO in a browser.
    """
    r = api_request("POST", "auth/ad/verify", body={"email": email})
    status = (r.get("meta") or {}).get("status")
    if status == 404:
        raise ApiError(404, "email is not registered")
    if status != 200:
        raise ApiError(status, (r.get("meta") or {}).get("message") or "verify failed")

    if (r.get("data") or {}).get("ad_status"):
        login_url = r["data"].get("login_url") or SAML_PREFIX
        if get_redirect is None:
            raise ApiError(0, "SSO login requires a redirect handler")
        final_url = get_redirect(login_url).strip()
        if not final_url.startswith(SAML_PREFIX):
            raise ApiError(0, "the pasted URL does not match the expected redirect prefix")
        r = api_request(
            "POST", "auth/ad/callback",
            body={"hash": final_url[len(SAML_PREFIX):], "fcm_token": fcm_token},
        )
    else:
        if not password and get_password is not None:
            password = get_password()
        if not password:
            raise ApiError(0, "password is required for this account")
        r = api_request(
            "POST", "auth/login",
            body={"email": email, "password": password, "fcm_token": fcm_token},
        )

    token = (r.get("data") or {}).get("token")
    if not token:
        raise ApiError(0, (r.get("meta") or {}).get("message") or "login failed")
    save_config({**load_config(), "token": token, "fcm_token": fcm_token or None})
    return token
