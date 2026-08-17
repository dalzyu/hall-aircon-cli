#!/usr/bin/env python3
"""hall_aircon.py — unofficial CLI for the Hall Aircon service.

Uses the same public HTTPS API the official mobile app uses, with your own
account credentials. No third-party dependencies (Python 3.8+, stdlib only).
"""

import argparse
import getpass
import sys
import time

import hall_aircon_api as api


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def resolve_token(args) -> str:
    token = args.token or api.get_token()
    if not token:
        die("no token found — run 'hall_aircon.py login' or pass --token")
    return token


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def _redirect_handler(login_url: str) -> str:
    print("Sign in with your NTU account in a browser:")
    print(f"  {login_url}")
    print()
    print("After sign-in you will be redirected to a URL starting with:")
    print(f"  {api.SAML_PREFIX}")
    return input("Paste that full redirect URL here: ")


def cmd_login(args) -> None:
    email = args.email or input("Email: ").strip()
    try:
        api.login(
            email, password=args.password, fcm_token=args.fcm_token or "",
            get_redirect=None if args.password else _redirect_handler,
        )
    except api.ApiError as e:
        die(str(e))
    print(f"logged in — token stored in {api.CONFIG_PATH} (mode 0600)")


def cmd_status(args) -> None:
    try:
        r = api.api_request("GET", "me", token=resolve_token(args))
    except api.ApiError as e:
        die(str(e))
    if (r.get("meta") or {}).get("status", 200) != 200:
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
        print(f"fan     : {a.get('fanstep') or 'unsupported'}")
        print(f"swing   : {a.get('flap') or 'unsupported'}")
        print(f"online  : {'yes' if a.get('comm_stat') else 'no'}")
        if a.get("maintenance_mode"):
            print("NOTE    : unit is in maintenance mode")


def cmd_control(key: str, value: str, args) -> None:
    token = resolve_token(args)
    try:
        r = api.api_request("POST", "v2/ac/control", token=token, body={key: value})
    except api.ApiError as e:
        die(str(e))
    meta = r.get("meta") or {}
    if meta.get("status", 200) != 200:
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
    if level not in api.FAN_LEVELS:
        die(f"fan level must be one of {', '.join(api.FAN_LEVELS)}")
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


def _list_request(args, path: str, what: str, columns) -> None:
    try:
        r = api.api_request("GET", path, token=resolve_token(args))
    except api.ApiError as e:
        die(str(e))
    if (r.get("meta") or {}).get("status", 200) != 200:
        die(f"{what} failed: {r.get('meta', {}).get('message')}")
    meta = r["meta"]
    print(f"{meta.get('rowCount', 0)} record(s)")
    _print_rows(r.get("data") or [], columns)


def cmd_usage(args) -> None:
    _list_request(
        args, f"usage/history?limit={args.limit}&offset={args.offset}", "usage",
        [("start", "starttime"), ("end", "endtime"), ("minutes", "duration"),
         ("cost", "amount"), ("rate/min", "rate"), ("aircon", "aircon_name")],
    )


def cmd_topups(args) -> None:
    _list_request(
        args, f"topup/history?limit={args.limit}", "topups",
        [("date", "created_on"), ("type", "type"), ("amount", "amount"),
         ("status", "status"), ("ref", "txn_id")],
    )


def cmd_inbox(args) -> None:
    _list_request(
        args, f"notification?limit={args.limit}", "inbox",
        [("sent", "send_on"), ("title", "title"), ("message", "message")],
    )


def cmd_logout(args) -> None:
    try:
        api.api_request("POST", "auth/logout", token=resolve_token(args))
    except api.ApiError as e:
        die(str(e))
    api.clear_token()
    print("logged out")


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
