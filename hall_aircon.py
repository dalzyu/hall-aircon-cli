#!/usr/bin/env python3
"""hall_aircon.py — unofficial CLI for the Hall Aircon service.

Uses the same public HTTPS API the official mobile app uses, with your own
account credentials. No third-party dependencies (Python 3.8+, stdlib only).
"""

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("HALL_AIRCON_API", "https://apintu-prod.daikinpayu.com").rstrip("/")
SAML_PREFIX = "https://cmsntu-prod.daikinpayu.com/adfs/saml/redirect/"
CONFIG_PATH = os.path.join(
    os.environ.get("HALL_AIRCON_CONFIG_DIR", os.path.expanduser("~/.config/hall-aircon")),
    "config.json",
)
# Same User-Agent the official app sends; the API host's edge blocks generic
# Python clients otherwise.
USER_AGENT = os.environ.get("HALL_AIRCON_USER_AGENT", "Dart/3.0 (dart:io)")

FAN_LEVELS = ("A", "L", "LM", "M", "MH", "H")


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def api_request(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
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
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"meta": {"status": e.code, "message": raw.strip() or e.reason}}
    except urllib.error.URLError as e:
        return {"meta": {"status": 0, "message": f"network error: {e.reason}"}}


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------
# Credential handling
# --------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass  # e.g. Windows


def resolve_token(args) -> str:
    token = args.token or os.environ.get("HALL_AIRCON_TOKEN") or load_config().get("token")
    if not token:
        die("no token found — run 'hall_aircon.py login' or pass --token")
    return token


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_login(args) -> None:
    email = args.email or input("Email: ").strip()
    fcm = args.fcm_token or ""
    password = args.password

    r = api_request("POST", "auth/ad/verify", body={"email": email})
    status = r.get("meta", {}).get("status")
    if status == 404:
        die("email is not registered")
    if status != 200:
        die(f"verify failed: {r.get('meta', {}).get('message')}")

    if r.get("data", {}).get("ad_status"):
        # Student account: NTU SSO via browser, then exchange the redirect hash.
        login_url = r["data"].get("login_url") or f"{SAML_PREFIX}"
        print("Sign in with your NTU account in a browser:")
        print(f"  {login_url}")
        print()
        print("After sign-in you will be redirected to a URL starting with:")
        print(f"  {SAML_PREFIX}")
        final_url = input("Paste that full redirect URL here: ").strip()
        if not final_url.startswith(SAML_PREFIX):
            die("the pasted URL does not match the expected redirect prefix")
        body = {"hash": final_url[len(SAML_PREFIX):], "fcm_token": fcm}
        r = api_request("POST", "auth/ad/callback", body=body)
    else:
        # Non-student account: email + password.
        if not password:
            password = getpass.getpass("Password: ")
        r = api_request(
            "POST", "auth/login",
            body={"email": email, "password": password, "fcm_token": fcm},
        )

    token = (r.get("data") or {}).get("token")
    if not token:
        die(f"login failed: {r.get('meta', {}).get('message') or r}")
    save_config({**load_config(), "token": token, "fcm_token": fcm or None})
    print(f"logged in — token stored in {CONFIG_PATH} (mode 0600)")


def cmd_status(args) -> None:
    r = api_request("GET", "me", token=resolve_token(args))
    if (r.get("meta") or {}).get("status") != 200:
        die(f"status failed: {r.get('meta', {}).get('message')}")
    d = r["data"]
    a = d.get("aircon") or {}
    print(f"balance : SGD {d.get('balance')}")
    if a:
        print(f"aircon  : {a.get('room_name') or a.get('aircon_code')} ({a.get('hall', {}).get('hall_name', '')})")
        print(f"power   : {'ON' if a.get('power') else 'OFF'}")
        print(f"mode    : {a.get('mode')}")
        print(f"setpoint: {a.get('setpoint')} C")
        print(f"current : {a.get('current_temperature')} C")
        print(f"fan     : {a.get('fanstep')}")
        print(f"swing   : {a.get('flap')}")
        print(f"online  : {'yes' if a.get('comm_stat') else 'no'}")
        if a.get("maintenance_mode"):
            print("NOTE    : unit is in maintenance mode")


def cmd_control(key: str, value: str, args) -> None:
    token = resolve_token(args)
    r = api_request("POST", "v2/ac/control", token=token, body={key: value})
    meta = r.get("meta") or {}
    if meta.get("status") != 200:
        die(f"control failed: {meta.get('message')}")
    print(meta.get("message", "ok"))
    time.sleep(3)
    cmd_status(args)


def cmd_on(args) -> None:
    cmd_control("power", "1", args)


def cmd_off(args) -> None:
    cmd_control("power", "0", args)


def cmd_temp(args) -> None:
    temp = int(args.temp)
    if not 16 <= temp <= 30:
        die("temperature must be between 16 and 30 C")
    cmd_control("setpoint", str(temp), args)


def cmd_fan(args) -> None:
    level = args.level.upper()
    if level not in FAN_LEVELS:
        die(f"fan level must be one of {', '.join(FAN_LEVELS)}")
    cmd_control("fanstep", level, args)


def cmd_swing(args) -> None:
    state = args.state.lower()
    if state not in ("on", "off"):
        die("swing state must be 'on' or 'off'")
    cmd_control("flap", "S" if state == "on" else "N", args)


def _print_rows(rows: list, columns: list[tuple[str, str]]) -> None:
    if not rows:
        print("(no records)")
        return
    for row in rows:
        print("  ".join(f"{label}: {row.get(key, '')}" for label, key in columns))
        print()


def cmd_usage(args) -> None:
    r = api_request(
        "GET", f"usage/history?limit={args.limit}&offset={args.offset}",
        token=resolve_token(args),
    )
    if (r.get("meta") or {}).get("status", 200) != 200:
        die(f"usage failed: {r.get('meta', {}).get('message')}")
    meta = r["meta"]
    print(f"{meta.get('rowCount', 0)} session(s)")
    _print_rows(
        r.get("data") or [],
        [("start", "starttime"), ("end", "endtime"), ("minutes", "duration"),
         ("cost", "amount"), ("rate/min", "rate"), ("aircon", "aircon_name")],
    )


def cmd_topups(args) -> None:
    r = api_request("GET", f"topup/history?limit={args.limit}", token=resolve_token(args))
    if (r.get("meta") or {}).get("status", 200) != 200:
        die(f"topups failed: {r.get('meta', {}).get('message')}")
    meta = r["meta"]
    print(f"{meta.get('rowCount', 0)} top-up(s)")
    _print_rows(
        r.get("data") or [],
        [("date", "created_on"), ("type", "type"), ("amount", "amount"),
         ("status", "status"), ("ref", "txn_id")],
    )


def cmd_inbox(args) -> None:
    r = api_request("GET", f"notification?limit={args.limit}", token=resolve_token(args))
    if (r.get("meta") or {}).get("status", 200) != 200:
        die(f"inbox failed: {r.get('meta', {}).get('message')}")
    meta = r["meta"]
    print(f"{meta.get('rowCount', 0)} message(s)")
    _print_rows(
        r.get("data") or [],
        [("sent", "send_on"), ("title", "title"), ("message", "message")],
    )


def cmd_logout(args) -> None:
    r = api_request("POST", "auth/logout", token=resolve_token(args))
    try:
        os.remove(CONFIG_PATH)
    except OSError:
        pass
    print((r.get("meta") or {}).get("message", "logged out"))


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hall_aircon.py",
        description="Unofficial CLI for the Hall Aircon service (uses the same public API as the official app).",
    )
    p.add_argument("--token", help="session token (overrides config file and HALL_AIRCON_TOKEN)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show wallet balance and aircon state")
    sub.add_parser("on", help="turn the aircon on")
    sub.add_parser("off", help="turn the aircon off")

    sp = sub.add_parser("temp", help="set temperature setpoint (16-30 C)")
    sp.add_argument("temp")

    sp = sub.add_parser("fan", help="set fan speed")
    sp.add_argument("level", help="A (auto), L, LM, M, MH, H")

    sp = sub.add_parser("swing", help="set swing/flap")
    sp.add_argument("state", help="on or off")

    sp = sub.add_parser("usage", help="usage history")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--offset", type=int, default=0)

    sp = sub.add_parser("topups", help="top-up history")
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("inbox", help="notifications/messages")
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("login", help="log in and store the session token")
    sp.add_argument("--email")
    sp.add_argument("--password", help="only for non-student accounts (prompted if needed)")
    sp.add_argument("--fcm-token", help="optional Firebase Cloud Messaging token for push notifications")

    sub.add_parser("logout", help="log out and delete the stored token")
    return p


def main() -> None:
    args = build_parser().parse_args()
    handlers = {
        "status": cmd_status, "on": cmd_on, "off": cmd_off, "temp": cmd_temp,
        "fan": cmd_fan, "swing": cmd_swing, "usage": cmd_usage, "topups": cmd_topups,
        "inbox": cmd_inbox, "login": cmd_login, "logout": cmd_logout,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
