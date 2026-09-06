#!/usr/bin/env python3
"""gui.py — cross-platform desktop GUI for the Hall Aircon service.

Runs on Windows, macOS and Linux. Requires: customtkinter
    pip install customtkinter

Features:
  - control: power, temperature, fan speed, swing
  - telemetry: room temperature, mode, online status, maintenance, balance,
    estimated cost of the current session
  - history: usage sessions (with cost) and top-ups
  - inbox: service notifications
  - automatic fan/swing support detection: if the unit never reports
    fanstep/flap, the controls are greyed out and labelled unsupported
"""

import math
import queue
import sys
import threading
import time
import webbrowser
from datetime import datetime

# Windows: opt into per-monitor DPI awareness before any window is created,
# otherwise Tk windows get bitmap-scaled by Windows and look blurry on
# high-DPI / mixed-DPI setups.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import customtkinter as ctk

import hall_aircon_api as api
from hall_aircon_version import __version__

HISTORY_EVERY = 6          # fetch history/inbox every 6th poll (~6 min)
DEFAULT_RATE = 0.0065      # SGD per minute fallback

GREEN = "#2E7D32"
GRAY = "#5A5A5A"
RED = "#C62828"
AMBER = "#F9A825"
MODE_NAMES = {"C": "Cool", "A": "Auto", "D": "Dry", "F": "Fan only", "H": "Heat"}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"Hall Aircon {__version__}")
        self.geometry("440x760")
        self.minsize(440, 420)

        self.account_state = None               # latest /me data
        self.temp_min, self.temp_max = 16, 30
        self.config_loaded = False
        self.busy = False
        self.tick = 0
        self.logged_in_email = None
        self.usage_cache, self.topup_cache, self.inbox_cache = [], [], []
        self._fan_supported = False
        self._swing_supported = False
        self._ui_queue = queue.Queue()  # thread -> main-thread callback queue

        # smart (bang-bang) mode state
        sc = api.load_config().get("smart") or {}
        self.model = api.load_config().get("thermal") or None
        self.smart_enabled = bool(sc.get("enabled")) and self.model is not None
        self.smart_target = max(23, min(26, int(sc.get("target", 24))))
        self.smart_margin = max(0.1, min(1.0, float(sc.get("margin", 0.2))))
        # asymmetric band support: off_at/on_at override the target±1 defaults
        self.smart_off_at = float(sc.get("off_at") or (self.smart_target - 1))
        self.smart_on_at = float(sc.get("on_at") or (self.smart_target + 1))
        st = api.load_config().get("smart_stats") or {"date": "", "on_s": 0, "win_s": 0}
        self._smart_stats = st
        self._smart_last_ts = time.time()
        self._smart_last_save = time.time()
        self._last_fetch_ts = 0.0          # last /me fetch (smart sparse polling)
        self._pending_off_id = None        # scheduled minute-boundary shutdown
        # thermal calibration state
        self.calib = api.load_config().get("calib") or {"phase": None, "samples": [], "started": 0}

        self._build_login()
        self._build_main()
        self.smart_target_lbl.configure(text=f"{self.smart_target}°C")
        self.margin_lbl.configure(text=f"{self.smart_margin:.1f}°C")
        if self.calib.get("phase"):
            self.calib_btn.configure(text="Cancel")
            self.calib_status.configure(text="Calibration in progress — samples will resume with each poll.")
        if self.smart_enabled:
            self.smart_switch.select()
        self._update_smart_label()

        self.show_login()
        if api.get_token():
            self.load_main()
        self._schedule_poll()
        self.after(100, self._drain_queue)

    def _drain_queue(self):
        """Run UI callbacks posted from worker threads (thread-safe)."""
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception:  # noqa: BLE001 — one bad callback must not kill the drain
                    pass
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ UI
    def _build_login(self):
        self.login_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.login_frame = ctk.CTkFrame(self.login_scroll)
        self.login_frame.pack(pady=20)

        ctk.CTkLabel(self.login_frame, text="Hall Aircon", font=ctk.CTkFont(size=28, weight="bold")).pack(pady=(28, 4))
        ctk.CTkLabel(self.login_frame, text="Sign in with your account", font=ctk.CTkFont(size=14)).pack(pady=(0, 20))

        self.email_entry = ctk.CTkEntry(self.login_frame, width=300, placeholder_text="you@e.ntu.edu.sg")
        self.email_entry.pack(pady=6)
        self.password_entry = ctk.CTkEntry(self.login_frame, width=300, placeholder_text="Password", show="*")

        self.sso_pane = ctk.CTkFrame(self.login_frame, fg_color="transparent")
        ctk.CTkLabel(self.sso_pane, text="1. Open the NTU sign-in page:", font=ctk.CTkFont(size=12)).pack(pady=(4, 0))
        self.open_browser_btn = ctk.CTkButton(self.sso_pane, text="Open login page in browser", width=300, command=self.open_sso_url)
        self.open_browser_btn.pack(pady=4)
        ctk.CTkLabel(self.sso_pane, text="2. Sign in, then paste the URL you are\nredirected to (it starts with cmsntu-prod...):",
                     font=ctk.CTkFont(size=12), justify="left").pack(pady=(6, 0))
        self.redirect_entry = ctk.CTkEntry(self.sso_pane, width=300, placeholder_text="Paste redirect URL here")
        self.redirect_entry.pack(pady=4)
        self.sso_finish_btn = ctk.CTkButton(self.sso_pane, text="Finish sign-in", width=300, command=self.finish_sso)
        self.sso_finish_btn.pack(pady=4)

        self.login_btn = ctk.CTkButton(self.login_frame, text="Continue", width=300, command=self.start_login)
        self.login_btn.pack(pady=10)
        self.back_btn = ctk.CTkButton(self.login_frame, text="Back", width=100, fg_color="transparent", border_width=1,
                                      command=self.show_email_step)
        self.login_error = ctk.CTkLabel(self.login_frame, text="", text_color="#ff8a80", wraplength=300, font=ctk.CTkFont(size=12))
        self.login_error.pack(pady=(4, 20))

    def _build_main(self):
        self.main_frame = ctk.CTkFrame(self)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(3, weight=1)

        self.header = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=20, weight="bold"), wraplength=390)
        self.header.grid(row=0, column=0, pady=(12, 0))
        self.sub_header = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=13), text_color="#9e9e9e", wraplength=390)
        self.sub_header.grid(row=1, column=0, pady=(0, 4))
        self.warn_label = ctk.CTkLabel(self.main_frame, text="", text_color=AMBER, font=ctk.CTkFont(size=13), wraplength=390)
        self.warn_label.grid(row=2, column=0, pady=(0, 2))

        self.tabs = ctk.CTkTabview(self.main_frame)
        self.tabs.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)
        self.tabs.add("Control")
        self.tabs.add("History")
        self.tabs.add("Inbox")
        self.control_scroll = ctk.CTkScrollableFrame(self.tabs.tab("Control"), fg_color="transparent")
        self.control_scroll.pack(fill="both", expand=True)
        self._build_control_tab(self.control_scroll)
        self.history_scroll = ctk.CTkScrollableFrame(self.tabs.tab("History"), fg_color="transparent")
        self.history_scroll.pack(fill="both", expand=True)
        self._build_history_tab(self.history_scroll)
        self._build_inbox_tab(self.tabs.tab("Inbox"))

        self.footer = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=12), text_color="#9e9e9e", wraplength=390)
        self.footer.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.logout_btn = ctk.CTkButton(self.main_frame, text="Log out", width=100, fg_color="transparent",
                                        border_width=1, command=self.do_logout)
        self.logout_btn.grid(row=4, column=0, pady=(4, 4))

    def _build_control_tab(self, tab):
        self.balance_label = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=15))
        self.balance_label.pack(pady=(10, 4))
        self.session_label = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color="#9e9e9e")
        self.session_label.pack(pady=(0, 6))

        # smart (bang-bang) mode: cool to target-1, restart at target+1
        smart_frame = ctk.CTkFrame(tab)
        smart_frame.pack(pady=(0, 8))
        self.smart_switch = ctk.CTkSwitch(smart_frame, text="Smart (experimental)", font=ctk.CTkFont(size=14),
                                          command=self._toggle_smart)
        self.smart_switch.pack(side="left", padx=10)
        self.smart_minus = ctk.CTkButton(smart_frame, text="−", width=30, height=28,
                                         command=lambda: self._smart_target_delta(-1))
        self.smart_minus.pack(side="left", padx=2)
        self.smart_target_lbl = ctk.CTkLabel(smart_frame, text="24°C", font=ctk.CTkFont(size=15, weight="bold"))
        self.smart_target_lbl.pack(side="left", padx=4)
        self.smart_plus = ctk.CTkButton(smart_frame, text="+", width=30, height=28,
                                        command=lambda: self._smart_target_delta(1))
        self.smart_plus.pack(side="left", padx=2)

        margin_frame = ctk.CTkFrame(tab)
        margin_frame.pack(pady=(0, 4))
        ctk.CTkLabel(margin_frame, text="Reaction margin", font=ctk.CTkFont(size=12)).pack(side="left", padx=8)
        self.margin_minus = ctk.CTkButton(margin_frame, text="−", width=28, height=24,
                                          command=lambda: self._margin_delta(-0.1))
        self.margin_minus.pack(side="left", padx=2)
        self.margin_lbl = ctk.CTkLabel(margin_frame, text="0.2°C", font=ctk.CTkFont(size=13, weight="bold"), width=52)
        self.margin_lbl.pack(side="left", padx=2)
        self.margin_plus = ctk.CTkButton(margin_frame, text="+", width=28, height=24,
                                         command=lambda: self._margin_delta(0.1))
        self.margin_plus.pack(side="left", padx=2)
        self.calib_btn = ctk.CTkButton(margin_frame, text="Calibrate", width=84, height=24,
                                       command=self._toggle_calibration)
        self.calib_btn.pack(side="left", padx=8)

        self.smart_status = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color=AMBER, wraplength=380)
        self.smart_status.pack(pady=(0, 2))
        self.calib_status = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color="#7ec8ff", wraplength=380)
        self.calib_status.pack(pady=(0, 2))
        ctk.CTkLabel(tab, text="Keep this app running for Smart mode, calibration and auto-refresh "
                              "to work (minimising the window is fine).",
                     font=ctk.CTkFont(size=11), text_color="#757575", wraplength=380).pack(pady=(0, 4))

        self.power_btn = ctk.CTkButton(tab, width=280, height=84, corner_radius=16,
                                       font=ctk.CTkFont(size=26, weight="bold"),
                                       command=self.toggle_power)
        self.power_btn.pack(pady=(2, 14))

        temp_row = ctk.CTkFrame(tab, fg_color="transparent")
        temp_row.pack(pady=4)
        self.temp_down = ctk.CTkButton(temp_row, text="−", width=52, height=52, font=ctk.CTkFont(size=24, weight="bold"),
                                       command=lambda: self.step_temp(-1))
        self.temp_down.pack(side="left", padx=8)
        temp_col = ctk.CTkFrame(temp_row, fg_color="transparent")
        temp_col.pack(side="left", padx=8)
        self.setpoint_label = ctk.CTkLabel(temp_col, text="--", font=ctk.CTkFont(size=46, weight="bold"))
        self.setpoint_label.pack()
        ctk.CTkLabel(temp_col, text="setpoint °C", font=ctk.CTkFont(size=12), text_color="#9e9e9e").pack()
        self.temp_up = ctk.CTkButton(temp_row, text="+", width=52, height=52, font=ctk.CTkFont(size=24, weight="bold"),
                                     command=lambda: self.step_temp(1))
        self.temp_up.pack(side="left", padx=8)

        self.current_label = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=13), text_color="#9e9e9e")
        self.current_label.pack(pady=(2, 10))

        ctk.CTkLabel(tab, text="Fan speed", font=ctk.CTkFont(size=13)).pack()
        fan_row = ctk.CTkFrame(tab, fg_color="transparent")
        fan_row.pack(pady=4)
        self.fan_btns = {}
        for i, level in enumerate(api.FAN_LEVELS):
            label = "Auto" if level == "A" else str(i)
            btn = ctk.CTkButton(fan_row, text=label, width=52, height=34,
                                command=lambda lv=level: self.set_fan(lv))
            btn.pack(side="left", padx=3)
            self.fan_btns[level] = btn
        self.fan_hint = ctk.CTkLabel(tab, text="Auto, 1 = low … 5 = high", font=ctk.CTkFont(size=11), text_color="#757575")
        self.fan_hint.pack(pady=(2, 10))

        self.swing_switch = ctk.CTkSwitch(tab, text="Swing (louver)", font=ctk.CTkFont(size=14),
                                          command=self.toggle_swing)
        self.swing_switch.pack(pady=4)
        self.swing_hint = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=11), text_color="#757575")
        self.swing_hint.pack(pady=(0, 12))

    def _build_history_tab(self, tab):
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(head, text="Usage sessions", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(head, text="Refresh", width=80, height=26, command=self._refresh_lists).pack(side="right")
        self.usage_summary = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color="#9e9e9e")
        self.usage_summary.pack(pady=(0, 4))
        self.usage_box = ctk.CTkFrame(tab, height=1, fg_color="transparent")
        self.usage_box.pack(fill="x", padx=10)

        ctk.CTkLabel(tab, text="Top-ups", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(12, 4))
        self.topup_box = ctk.CTkFrame(tab, height=1, fg_color="transparent")
        self.topup_box.pack(fill="x", padx=10, pady=(0, 10))
        self._render_usage([])
        self._render_topups([])

    def _build_inbox_tab(self, tab):
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(head, text="Notifications", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(head, text="Refresh", width=80, height=26, command=self._refresh_lists).pack(side="right")
        self.inbox_box = ctk.CTkScrollableFrame(tab)
        self.inbox_box.pack(fill="both", expand=True, padx=10, pady=10)

    # -------------------------------------------------------------- screens
    def show_login(self):
        self.main_frame.pack_forget()
        self.login_scroll.pack(fill="both", expand=True)
        self.show_email_step()

    def show_email_step(self):
        self.password_entry.pack_forget()
        self.sso_pane.pack_forget()
        self.back_btn.pack_forget()
        self.login_btn.pack(pady=10)
        self.login_error.configure(text="")

    def show_main(self):
        self.login_scroll.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def _set_login_error(self, message: str):
        self.login_error.configure(text=message)

    # -------------------------------------------------------------- threads
    def _run(self, fn, on_ok=None, on_err=None):
        def worker():
            try:
                result = fn()
            except api.ApiError as e:
                self._ui_queue.put(lambda message=str(e): on_err(message) if on_err else None)
                return
            except Exception as e:  # noqa: BLE001
                self._ui_queue.put(lambda message=str(e): on_err(message) if on_err else None)
                return
            self._ui_queue.put(lambda: on_ok(result) if on_ok else None)
        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------- login
    def start_login(self):
        email = self.email_entry.get().strip()
        if not email or "@" not in email:
            self._set_login_error("Please enter a valid email address.")
            return
        self.logged_in_email = email
        self._set_login_error("Checking account…")
        self._run(
            lambda: api.api_request("POST", "auth/ad/verify", body={"email": email}),
            on_ok=lambda r: self._after_verify(r),
            on_err=lambda e: self._set_login_error(e),
        )

    def _after_verify(self, r):
        data = r.get("data") or {}
        self.login_btn.pack_forget()
        if data.get("ad_status"):
            self.sso_url = data.get("login_url") or api.SAML_PREFIX
            self.sso_pane.pack(pady=4)
            self.back_btn.pack(pady=4)
            self._set_login_error("")
        else:
            self.password_entry.pack(pady=6)
            self.login_btn.configure(text="Log in", command=self.do_password_login)
            self.login_btn.pack(pady=10)
            self.back_btn.pack(pady=4)
            self._set_login_error("")

    def open_sso_url(self):
        webbrowser.open(self.sso_url)

    def finish_sso(self):
        final_url = self.redirect_entry.get().strip()
        if not final_url.startswith(api.SAML_PREFIX):
            self._set_login_error("That URL doesn't look right — it should start with:\n" + api.SAML_PREFIX)
            return
        self._set_login_error("Signing in…")
        self._run(
            lambda: api.login(self.logged_in_email, get_redirect=lambda _u: final_url),
            on_ok=lambda _t: self._after_login(),
            on_err=lambda e: self._set_login_error(e),
        )

    def do_password_login(self):
        password = self.password_entry.get()
        if not password:
            self._set_login_error("Please enter your password.")
            return
        self._set_login_error("Signing in…")
        self._run(
            lambda: api.login(self.logged_in_email, password=password),
            on_ok=lambda _t: self._after_login(),
            on_err=lambda e: self._set_login_error(e),
        )

    def _after_login(self):
        self._set_login_error("")
        self.config_loaded = False
        self.load_main()

    def do_logout(self):
        self._set_smart(False)
        self.calib = {"phase": None}
        self._run(
            lambda: api.api_request("POST", "auth/logout", token=api.get_token()),
            on_ok=lambda _r: (api.clear_token(), self._reset_to_login()),
            on_err=lambda _e: (api.clear_token(), self._reset_to_login()),
        )

    def _reset_to_login(self):
        self.account_state = None
        self.show_login()

    # -------------------------------------------------------------- data
    def load_main(self):
        self.show_main()
        self.refresh(initial=True)

    def refresh(self, initial=False):
        token = api.get_token()
        if not token:
            return

        def on_ok(data):
            self._render(data)
            if initial:
                self._refresh_lists()
        self._run(
            lambda: self._fetch_all(token),
            on_ok=on_ok,
            on_err=lambda e: self._render_error(e, initial),
        )

    def _fetch_all(self, token):
        me = api.api_request("GET", "me", token=token)
        if not self.config_loaded:
            try:
                lo = api.api_request("GET", "app-config/temperature_allowed_min", token=token)
                # user-facing floor is 23 C (smart mode still uses 22 internally)
                self.temp_min = max(int(lo["data"]["value"]), 23)
            except (api.ApiError, KeyError, TypeError, ValueError):
                self.temp_min = 23
            try:
                hi = api.api_request("GET", "app-config/temperature_allowed_max", token=token)
                self.temp_max = int(hi["data"]["value"])
            except (api.ApiError, KeyError, TypeError, ValueError):
                pass
            self.config_loaded = True
        return me

    def _fetch_lists(self, token):
        usage = api.api_request("GET", "usage/history?limit=20", token=token)
        topups = api.api_request("GET", "topup/history?limit=10", token=token)
        inbox = api.api_request("GET", "notification?limit=20", token=token)
        return usage, topups, inbox

    # -------------------------------------------------------------- render
    def _render(self, me):
        data = me.get("data") or {}
        self.account_state = data
        a = data.get("aircon") or {}
        code = a.get("aircon_code") or "?"

        self._track_session(a)
        self._update_feature_support(a)

        hall = (a.get("hall") or {}).get("hall_name", "")
        room = a.get("room_name") or a.get("aircon_code") or ""
        dot = "● " if a.get("comm_stat") else "○ "
        self.header.configure(text=f"{dot}{hall} · {room}" if hall else f"{dot}{room or 'No aircon paired'}")
        self.balance_label.configure(text=f"Balance: SGD {data.get('balance', '—')}")

        if not a:
            self.sub_header.configure(text="Pair an aircon in the official app first")
            self.warn_label.configure(text="")
            self._set_controls_enabled(False)
        elif a.get("maintenance_mode"):
            self.sub_header.configure(text="")
            self.warn_label.configure(text="⚠ The aircon is under maintenance")
            self._set_controls_enabled(False)
        elif not a.get("comm_stat"):
            self.sub_header.configure(text="")
            self.warn_label.configure(text="⚠ Aircon is offline — commands may not reach it")
            self._set_controls_enabled(False)
        else:
            self.sub_header.configure(text="")
            self.warn_label.configure(text="")
            self._set_controls_enabled(True)

        power_on = bool(a.get("power"))
        self.power_btn.configure(
            text="ON" if power_on else "OFF",
            fg_color=GREEN if power_on else GRAY,
            hover_color=("#1B5E20" if power_on else "#424242"),
        )
        setpoint = a.get("setpoint")
        self.setpoint_label.configure(text=str(setpoint) if setpoint is not None else "--")
        cur = a.get("current_temperature")
        mode = MODE_NAMES.get(a.get("mode") or "", a.get("mode") or "")
        parts = []
        if cur is not None:
            parts.append(f"Room: {cur}°C")
        if mode:
            parts.append(mode)
        self.current_label.configure(text="  ·  ".join(parts) if parts else "")
        self.temp_down.configure(state="normal" if power_on and setpoint and setpoint > self.temp_min else "disabled")
        self.temp_up.configure(state="normal" if power_on and setpoint is not None and setpoint < self.temp_max else "disabled")

        fanstep = a.get("fanstep")
        for level, btn in self.fan_btns.items():
            btn.configure(fg_color=("#1F6AA5" if level == fanstep else "#3B3B3B"),
                          hover_color=("#1F6AA5" if level == fanstep else "#4a4a4a"))
        flap = a.get("flap")
        if flap in ("S", "N"):
            self.swing_switch.select() if flap == "S" else self.swing_switch.deselect()

        self._update_session_label(a)
        self._smart_tick(a)
        self._calibration_tick(a)
        self.footer.configure(text=f"Updated {datetime.now().strftime('%H:%M:%S')}")
        self._render_cached_lists()

    def _render_error(self, message, initial):
        self.footer.configure(text=message)
        if initial:
            self.header.configure(text="Could not load data")
            self.sub_header.configure(text=message)

    # -------------------------------------------------------------- telemetry extras
    def _track_session(self, a):
        """Remember when the unit switched on, so we can estimate live cost."""
        code = a.get("aircon_code")
        config = api.load_config()
        sessions = config.setdefault("session_start", {})
        changed = False
        if a.get("power") and code and code not in sessions:
            sessions[code] = time.time()
            changed = True
        elif not a.get("power") and code in sessions:
            sessions.pop(code, None)
            changed = True
        if changed:
            config["session_start"] = sessions
            api.save_config(config)

    def _session_rate(self):
        for row in self.usage_cache:
            if isinstance(row.get("rate"), (int, float)) and row.get("rate") > 0:
                return float(row["rate"])
        return DEFAULT_RATE

    def _update_session_label(self, a):
        code = a.get("aircon_code")
        start = (api.load_config().get("session_start") or {}).get(code)
        if a.get("power") and start:
            minutes = (time.time() - start) / 60
            cost = minutes * self._session_rate()
            self.session_label.configure(text=f"Running {int(minutes)} min · ≈ SGD {cost:.2f} "
                                             f"(rate SGD {self._session_rate():.4f}/min)")
        else:
            self.session_label.configure(text="")

    # -------------------------------------------------------------- smart (bang-bang) mode
    def _toggle_smart(self):
        self._set_smart(bool(self.smart_switch.get()))

    def _set_smart(self, enabled: bool):
        if enabled and self.model is None:
            self.smart_switch.deselect()
            self.calib_status.configure(text="Smart mode needs a thermal model first — press Calibrate.")
            return
        self.smart_enabled = enabled
        if not enabled and self._pending_off_id is not None:
            self.after_cancel(self._pending_off_id)
            self._pending_off_id = None
        config = api.load_config()
        config["smart"] = {
            "enabled": enabled, "target": self.smart_target, "margin": self.smart_margin,
            "off_at": self.smart_off_at, "on_at": self.smart_on_at,
        }
        api.save_config(config)
        if enabled:
            self.smart_switch.select()
            self._smart_stats_reset_if_needed()
            self._last_fetch_ts = 0  # fetch on the next tick to anchor state
            self._update_smart_label()
        else:
            self.smart_switch.deselect()
            self.smart_status.configure(text="Smart mode off")
            self._update_smart_label()

    def _smart_target_delta(self, delta):
        # user-facing target range 23..26; smart cools to 22 internally.
        # Moving the target shifts the whole (possibly asymmetric) band.
        new = max(23, min(26, self.smart_target + delta))
        if new == self.smart_target:
            return
        off_gap = self.smart_target - self.smart_off_at
        on_gap = self.smart_on_at - self.smart_target
        self.smart_target = new
        self.smart_off_at = new - off_gap
        self.smart_on_at = new + on_gap
        self.smart_target_lbl.configure(text=f"{new}°C")
        config = api.load_config()
        config["smart"] = {
            "enabled": self.smart_enabled, "target": new, "margin": self.smart_margin,
            "off_at": self.smart_off_at, "on_at": self.smart_on_at,
        }
        api.save_config(config)
        self._update_smart_label()

    def _margin_delta(self, delta):
        new = round(max(0.1, min(1.0, self.smart_margin + delta)), 1)
        if new == self.smart_margin:
            return
        self.smart_margin = new
        self.margin_lbl.configure(text=f"{new:.1f}°C")
        config = api.load_config()
        config["smart"] = {
            "enabled": self.smart_enabled, "target": self.smart_target, "margin": new,
            "off_at": self.smart_off_at, "on_at": self.smart_on_at,
        }
        api.save_config(config)
        self._update_smart_label()

    def _smart_stats_reset_if_needed(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._smart_stats.get("date") != today:
            self._smart_stats = {"date": today, "on_s": 0, "win_s": 0}
            self._smart_last_save = time.time()

    def _smart_stats_accumulate(self, power_on: bool):
        now = time.time()
        delta = max(1, int(now - self._smart_last_ts))
        self._smart_last_ts = now
        self._smart_stats["win_s"] = int(self._smart_stats.get("win_s", 0)) + delta
        if power_on:
            self._smart_stats["on_s"] = int(self._smart_stats.get("on_s", 0)) + delta
        if now - self._smart_last_save > 60:
            config = api.load_config()
            config["smart_stats"] = self._smart_stats
            api.save_config(config)
            self._smart_last_save = now

    def _update_smart_label(self):
        if self.model:
            m = self.model
            summary = (f"model: T_amb {m.get('T_amb'):.1f} · τ_off {m.get('tau_off')/3600:.1f}h · "
                       f"T_eq {m.get('T_eq'):.1f} · τ_on {m.get('tau_on')/3600:.1f}h · lag {m.get('lag'):.0f}s")
        else:
            summary = "no thermal model yet — press Calibrate to unlock Smart mode"
        on_s = int(self._smart_stats.get("on_s", 0))
        win_s = max(int(self._smart_stats.get("win_s", 0)), 1)
        duty = 100.0 * on_s / win_s
        saved = (win_s - on_s) / 60.0 * self._session_rate()
        parts = [summary]
        if self.smart_enabled:
            parts.append(
                f"Smart ON @{self.smart_target}°C (band {self.smart_off_at:g}–{self.smart_on_at:g}, "
                f"margin {self.smart_margin:.1f}°C) — polls only near switches")
            if win_s >= 60:
                parts.append(f"duty {duty:.0f}% · ≈ SGD {saved:.2f} saved since {self._smart_stats.get('date', '')}")
        self.smart_status.configure(text="\n".join(parts))

    # -------------------------------------------------------------- thermal model
    def _smart_schedule(self, a):
        """Model-based prediction. Returns (kind, wait_seconds):
        off_boundary (temp already below low), off_lead (predicted time until
        low is reached), on (temp above high), on_lead (predicted until high),
        or None when nothing to do."""
        T = a.get("current_temperature")
        m = self.model
        if T is None or m is None or not a.get("comm_stat") or a.get("maintenance_mode"):
            return None
        low = self.smart_off_at
        high = self.smart_on_at
        tau_on = max(float(m.get("tau_on") or 3600), 300)
        tau_off = max(float(m.get("tau_off") or 21600), 600)
        T_eq = float(m.get("T_eq") or 24)
        T_amb = float(m.get("T_amb") or 28)
        if a.get("power"):
            if T <= low:
                return ("off_boundary", 0)
            denom = T - T_eq
            if denom <= 0.3:
                return ("off_boundary", 0)
            t_off = tau_on * math.log(denom / max(low - T_eq, 0.2))
            lead = self.smart_margin * tau_on / denom
            return ("off_lead", max(t_off - lead, 0.0))
        else:
            if T >= high:
                return ("on", 0)
            denom = T_amb - T
            if denom <= 0.3:
                return ("on", 0)
            t_on = tau_off * math.log(denom / max(T_amb - high, 0.2))
            lead = self.smart_margin * tau_off / denom
            return ("on_lead", max(t_on - lead, 0.0))

    def _smart_needs_poll(self):
        """Sparse polling: only fetch /me when near a predicted switch or the
        safety interval (15 min) has elapsed."""
        now = time.time()
        if now - self._last_fetch_ts >= 900:
            return True
        a = (self.account_state or {}).get("aircon") or {}
        if not a:
            return True
        s = self._smart_schedule(a)
        if s is None:
            return False
        kind, wait = s
        return kind.endswith("lead") and wait <= now - self._last_fetch_ts

    def _smart_tick(self, a):
        """Controller: act on the latest state, using the thermal model."""
        if not (self.smart_enabled and self.model) or self.busy:
            return
        if a.get("current_temperature") is None or not a.get("comm_stat") or a.get("maintenance_mode"):
            return
        self._smart_stats_reset_if_needed()
        self._smart_stats_accumulate(bool(a.get("power")))
        s = self._smart_schedule(a)
        if s is None:
            return
        kind, _wait = s
        if kind == "off_boundary":
            self._schedule_off_at_boundary(a)
        elif kind == "on":
            self._send({"power": "1", "setpoint": "22"},
                       success_note=f"Smart: warmed to {a.get('current_temperature')}°C, turning on (setpoint 22)")

    def _schedule_off_at_boundary(self, a):
        """Send the OFF so the gateway timestamps it just before a whole-minute
        boundary: billing rounds UP, so ending at X:00 - 1s pays X minutes."""
        code = a.get("aircon_code")
        start = (api.load_config().get("session_start") or {}).get(code)
        lag = float((self.model or {}).get("lag") or 6.0)
        now = time.time()
        if start is None:
            self._send({"power": "0"}, success_note="Smart: cooled to target, turning off (no alignment)")
            return
        k = math.floor((now - start) / 60) + 1
        t_send = start + k * 60 - lag - 1.0
        if t_send <= now + 1.0:
            t_send = start + (k + 1) * 60 - lag - 1.0
        delay_ms = max(500, int((t_send - now) * 1000))
        if self._pending_off_id is not None:
            self.after_cancel(self._pending_off_id)
        self._pending_off_id = self.after(delay_ms, self._send_smart_off)
        self.calib_status.configure(
            text=f"Smart: temp {a.get('current_temperature')}°C ≤ {self.smart_off_at:g}°C — "
                 f"turning off in {delay_ms/1000:.0f}s (minute-aligned)")

    def _send_smart_off(self):
        self._pending_off_id = None
        if not self.smart_enabled or not api.get_token():
            return
        a = (self.account_state or {}).get("aircon") or {}
        if not a.get("comm_stat") or a.get("maintenance_mode") or not a.get("power"):
            return
        self._send({"power": "0"}, success_note="Smart: off at minute boundary")

    # -------------------------------------------------------------- thermal calibration
    def _toggle_calibration(self):
        if self.calib.get("phase"):
            self._cancel_calibration()
            return
        if self.busy:
            return
        if self.smart_enabled and self.model:
            # in-flight calibration: refit from Smart's real cycles
            self._show_flight_warning()
        else:
            self._show_calibration_warning()

    def _show_flight_warning(self):
        win = ctk.CTkToplevel(self)
        win.title("In-flight calibration")
        win.transient(self)
        win.grab_set()
        win.geometry("460x330")
        ctk.CTkLabel(win, text="Calibrate while Smart runs?",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(18, 10))
        msg = (
            "The app will watch Smart mode's real cycles — collecting "
            "warm-up samples while the unit is off and cooling samples while "
            "it runs — and continuously refit the thermal model.\n\n"
            "Keep every heat source and fan in a FIXED state for the whole "
            "test, and keep this app open.\n\n"
            "No commands are sent by calibration itself; Smart keeps running."
        )
        ctk.CTkLabel(win, text=msg, justify="left", wraplength=400,
                     font=ctk.CTkFont(size=13)).pack(padx=22, pady=(0, 14))
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=(0, 18))
        ctk.CTkButton(btns, text="Cancel", width=120, fg_color="transparent", border_width=1,
                      command=win.destroy).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="Start in-flight calibration", width=200,
                      command=lambda: (win.destroy(), self._start_flight_calibration())).pack(side="left", padx=8)

    def _start_flight_calibration(self):
        self.calib = {"phase": "F", "drift": [], "cool": [], "started": time.time()}
        self._save_calib()
        self.calib_btn.configure(text="Cancel")
        self.calib_status.configure(
            text="In-flight calibration started — collecting warm-up & cooling samples from Smart's cycles…")

    def _show_calibration_warning(self):
        win = ctk.CTkToplevel(self)
        win.title("Before calibration")
        win.transient(self)
        win.grab_set()
        win.geometry("460x360")
        ctk.CTkLabel(win, text="⚠ Please check before starting",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(18, 10))
        msg = (
            "1. Your room should already be at steady state with the "
            "aircon OFF — off for at least 4 hours before calibrating.\n\n"
            "2. Keep every heat-generating source ON for the whole "
            "calibration (fridge, computer, chargers, lights, etc.).\n\n"
            "3. If possible, all occupants of the room should stay in the "
            "room for the whole calibration.\n\n"
            "4. Keep this app open for the whole calibration — closing it "
            "stops the sampling.\n\n"
            "The process takes roughly 1–2 hours: it first watches the room "
            "warm up, then turns the aircon on and watches it cool down."
        )
        ctk.CTkLabel(win, text=msg, justify="left", wraplength=400,
                     font=ctk.CTkFont(size=13)).pack(padx=22, pady=(0, 14))
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=(0, 18))
        ctk.CTkButton(btns, text="Cancel", width=120, fg_color="transparent", border_width=1,
                      command=win.destroy).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="I'm ready — start", width=180,
                      command=lambda: (win.destroy(), self._start_calibration())).pack(side="left", padx=8)

    def _start_calibration(self):
        # phase A: watch the room warm up with the unit OFF
        self.calib = {"phase": "A", "samples": [], "started": time.time()}
        self._save_calib()
        self.calib_btn.configure(text="Cancel")
        self.calib_status.configure(
            text="Calibration A: make sure the aircon is OFF. Watching the room warm up "
                 "(1 sample/min, needs ~15 samples and ≥0.4°C rise)…")
        a = (self.account_state or {}).get("aircon") or {}
        if a.get("power"):
            self._send({"power": "0"}, success_note="Calibration: unit turned off for drift measurement")

    def _cancel_calibration(self):
        self.calib = {"phase": None, "samples": [], "drift": [], "cool": [], "started": 0}
        self._save_calib()
        self.calib_btn.configure(text="Calibrate")
        self.calib_status.configure(text="Calibration cancelled")

    def _save_calib(self):
        config = api.load_config()
        config["calib"] = self.calib
        api.save_config(config)

    def _calibration_tick(self, a):
        phase = self.calib.get("phase")
        if not phase:
            return
        T = a.get("current_temperature")
        if T is None:
            return
        now = time.time()
        if phase == "F":
            # in-flight: tag each sample with the unit's power state
            bucket = "drift" if not a.get("power") else "cool"
            lst = self.calib.setdefault(bucket, [])
            if not lst or lst[-1][1] != T or now - lst[-1][0] >= 60:
                lst.append([now, T])
                self._save_calib()
                self._flight_fit(bucket)
            self.calib_status.configure(
                text=f"In-flight cal: {len(self.calib.get('drift', []))} warm-up, "
                     f"{len(self.calib.get('cool', []))} cooling samples · "
                     f"temp {T}°C ({'unit on' if a.get('power') else 'unit off'})")
            return
        samples = self.calib.setdefault("samples", [])
        if not samples or samples[-1][1] != T or now - samples[-1][0] >= 60:
            samples.append([now, T])
            self._save_calib()
        temps = [s[1] for s in samples]
        spread = max(temps) - min(temps)
        mins = len(samples)
        if phase == "A":
            self.calib_status.configure(
                text=f"Calibration A: {mins} samples, temp {T}°C (spread {spread:.0f}°C / need 0.4)…")
            if mins >= 15 and spread >= 0.4:
                self._finish_calib_a(temps, samples)
        elif phase == "B":
            self.calib_status.configure(
                text=f"Calibration B: {mins} samples, temp {T}°C (spread {spread:.0f}°C / need 0.4)…")
            if mins >= 15 and spread >= 0.4:
                self._finish_calib_b(temps, samples)

    def _flight_fit(self, bucket):
        """Continuously refit one model side from in-flight samples."""
        lst = self.calib.get(bucket) or []
        if len(lst) < 10:
            return
        temps = [s[1] for s in lst]
        if max(temps) - min(temps) < 1.0:
            return
        if bucket == "drift":
            T_amb, tau_off = self._fit_curve(lst, rising=True)
            if T_amb is None or tau_off is None:
                return
            self.model["T_amb"], self.model["tau_off"] = T_amb, tau_off
        else:
            T_eq, tau_on = self._fit_curve(lst, rising=False)
            if T_eq is None or tau_on is None:
                return
            self.model["T_eq"], self.model["tau_on"] = T_eq, tau_on
        config = api.load_config()
        config["thermal"] = self.model
        api.save_config(config)
        # keep a sliding window so the tail still pins the asymptote
        self.calib[bucket] = lst[-30:]
        self._save_calib()
        self._update_smart_label()

    def _finish_calib_a(self, temps, samples):
        T_amb, tau_off = self._fit_curve(samples, rising=True)
        if tau_off is None or T_amb is None:
            self.calib_status.configure(text="Calibration A data too flat — keep waiting, or restart later.")
            return
        self._calib_a = {"T_amb": T_amb, "tau_off": tau_off}
        # phase B: unit ON at setpoint 22, watch it cool
        self.calib = {"phase": "B", "samples": [], "started": time.time()}
        self._save_calib()
        self.calib_status.configure(
            text=f"Calibration B: drift fit done (T_amb {T_amb:.1f}°C, τ_off {tau_off/3600:.1f}h). "
                 "Turning the aircon ON at 22°C and watching it cool…")
        self._send({"power": "1", "setpoint": "22"}, success_note="Calibration: cooling phase started")

    def _finish_calib_b(self, temps, samples):
        T_eq, tau_on = self._fit_curve(samples, rising=False)
        if T_eq is None or tau_on is None:
            self.calib_status.configure(text="Calibration B data too flat — keep waiting, or restart later.")
            return
        model = {
            "T_amb": self._calib_a["T_amb"], "tau_off": self._calib_a["tau_off"],
            "T_eq": T_eq, "tau_on": tau_on,
            "lag": float((self.model or {}).get("lag") or 6.0),
            "fitted_at": time.strftime("%Y-%m-%d %H:%M"),
        }
        config = api.load_config()
        config["thermal"] = model
        api.save_config(config)
        self.model = model
        self.calib = {"phase": None, "samples": [], "started": 0}
        self._save_calib()
        self.calib_btn.configure(text="Calibrate")
        self.calib_status.configure(
            text=f"Calibration complete! T_amb {model['T_amb']:.1f}°C, τ_off {model['tau_off']/3600:.1f}h, "
                 f"T_eq {model['T_eq']:.1f}°C, τ_on {model['tau_on']/3600:.1f}h. Smart mode unlocked.")
        self._update_smart_label()

    def _fit_curve(self, samples, rising=True):
        """Fit T(t) = A - B·e^(-t/τ) (rising) or T(t) = A + B·e^(-t/τ) (falling)
        to 1°C-quantised samples. A small brute-force grid search in the
        original domain is far more robust to quantisation than log-space
        regression. Returns (asymptote, tau_seconds)."""
        if len(samples) < 8:
            return None, None
        t0 = samples[0][0]
        xs = [(s[0] - t0) / 3600.0 for s in samples]
        temps = [float(s[1]) for s in samples]
        T0 = temps[0]
        lo, hi = min(temps), max(temps)
        if hi - lo < 1.0:
            return None, None  # nothing happened yet
        if rising:
            a_candidates = [hi + 0.2 + 0.1 * i for i in range(14)]  # hi+0.2 .. hi+1.5
        else:
            a_candidates = [lo - 0.2 - 0.1 * i for i in range(14)]  # lo-0.2 .. lo-1.5
        tau_candidates = [0.25 * (1.55 ** i) for i in range(22)]    # 0.25h .. ~44h
        best = None
        for A in a_candidates:
            for tau in tau_candidates:
                sse = 0.0
                for x, y in zip(xs, temps):
                    pred = (A - (A - T0) * math.exp(-x / tau)) if rising \
                        else (A + (T0 - A) * math.exp(-x / tau))
                    sse += (pred - y) ** 2
                if best is None or sse < best[0]:
                    best = (sse, A, tau)
        if best is None:
            return None, None
        return best[1], best[2] * 3600

    def _refine_lag(self, usage_rows):
        """Measure command→gateway-timestamp lag from completed sessions and
        our logged command times."""
        if not isinstance(usage_rows, list) or not usage_rows:
            return
        cmd_log = api.load_config().get("cmd_log") or []
        if not cmd_log or not self.model:
            return
        row = usage_rows[0]
        try:
            start_ts = datetime.fromisoformat(row["starttime"]).timestamp()
            end_ts = datetime.fromisoformat(row["endtime"]).timestamp()
        except (KeyError, ValueError):
            return
        lags = []
        for cmd in cmd_log:
            body = cmd.get("body") or {}
            if body.get("power") == "1" and abs(cmd["t"] - start_ts) < 120:
                lags.append(start_ts - cmd["t"])
            if body.get("power") == "0" and abs(cmd["t"] - end_ts) < 120:
                lags.append(end_ts - cmd["t"])
        if lags:
            new_lag = sum(lags) / len(lags)
            if 0.5 <= new_lag <= 30:
                self.model["lag"] = new_lag
                config = api.load_config()
                config["thermal"] = self.model
                api.save_config(config)

    # -------------------------------------------------------------- feature support
    def _update_feature_support(self, a):
        """Simple rule: if the unit reports fanstep/flap it supports them;
        null means the hardware doesn't expose the feature."""
        self._fan_supported = a.get("fanstep") is not None
        self._swing_supported = a.get("flap") is not None
        if self._fan_supported:
            self.fan_hint.configure(text="Auto, 1 = low … 5 = high")
            for btn in self.fan_btns.values():
                btn.configure(state="normal")
        else:
            self.fan_hint.configure(text="Fan speed is not supported by this unit", text_color=AMBER)
            for btn in self.fan_btns.values():
                btn.configure(state="disabled")
        if self._swing_supported:
            self.swing_hint.configure(text="")
            self.swing_switch.configure(state="normal")
        else:
            self.swing_hint.configure(text="Swing is not supported by this unit", text_color=AMBER)
            self.swing_switch.configure(state="disabled")

    def _set_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.power_btn.configure(state=state)
        for btn in self.fan_btns.values():
            btn.configure(state=state if (enabled and self._fan_supported) else "disabled")
        if enabled and self._swing_supported:
            self.swing_switch.configure(state="normal")
        else:
            self.swing_switch.configure(state="disabled")
        if enabled:
            self.temp_down.configure(state="normal")
            self.temp_up.configure(state="normal")
        else:
            self.temp_down.configure(state="disabled")
            self.temp_up.configure(state="disabled")

    # -------------------------------------------------------------- lists
    def _render_cached_lists(self):
        if self.usage_cache:
            self._render_usage(self.usage_cache)
        if self.topup_cache:
            self._render_topups(self.topup_cache)
        if self.inbox_cache:
            self._render_inbox(self.inbox_cache)

    def _render_usage(self, rows):
        today = datetime.now().strftime("%Y-%m-%d")
        today_min = sum(float(r.get("duration") or 0) for r in rows if (r.get("starttime") or "").startswith(today))
        today_cost = sum(float(r.get("amount") or 0) for r in rows if (r.get("starttime") or "").startswith(today))
        self.usage_summary.configure(text=f"Today: {int(today_min)} min · SGD {today_cost:.2f}")

        for w in self.usage_box.winfo_children():
            w.destroy()
        for row in rows[:20]:
            start = (row.get("starttime") or "")[5:16]
            mins = row.get("duration")
            cost = row.get("amount")
            text = f"{start}   {mins} min   SGD {cost}"
            ctk.CTkLabel(self.usage_box, text=text, anchor="w", font=ctk.CTkFont(size=12)).pack(fill="x", pady=1)
        if not rows:
            ctk.CTkLabel(self.usage_box, text="No usage yet", text_color="#757575").pack(anchor="w")

    def _render_topups(self, rows):
        for w in self.topup_box.winfo_children():
            w.destroy()
        for row in rows[:10]:
            date = (row.get("created_on") or "")[:10]
            text = f"{date}   {row.get('type')}   SGD {row.get('amount')}   ref {row.get('txn_id')}"
            ctk.CTkLabel(self.topup_box, text=text, anchor="w", font=ctk.CTkFont(size=12)).pack(fill="x", pady=1)
        if not rows:
            ctk.CTkLabel(self.topup_box, text="No top-ups yet", text_color="#757575").pack(anchor="w")

    def _render_inbox(self, rows):
        for w in self.inbox_box.winfo_children():
            w.destroy()
        for row in rows[:20]:
            card = ctk.CTkFrame(self.inbox_box)
            card.pack(fill="x", pady=3)
            ctk.CTkLabel(card, text=row.get("title") or "(no title)", anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(fill="x", padx=8, pady=(6, 0))
            ctk.CTkLabel(card, text=row.get("message") or "", anchor="w", justify="left",
                         wraplength=360, font=ctk.CTkFont(size=12)).pack(fill="x", padx=8, pady=(0, 2))
            ctk.CTkLabel(card, text=(row.get("send_on") or "")[:16], anchor="w",
                         text_color="#757575", font=ctk.CTkFont(size=11)).pack(fill="x", padx=8, pady=(0, 6))
        if not rows:
            ctk.CTkLabel(self.inbox_box, text="No messages", text_color="#757575").pack(anchor="w")

    # -------------------------------------------------------------- actions
    def _send(self, body, success_note=""):
        if self.busy:
            return
        self.busy = True
        self.footer.configure(text="Sending…")
        token = api.get_token()
        # log command time for lag calibration (aligns minute-boundary shutdowns)
        config = api.load_config()
        cmd_log = config.get("cmd_log") or []
        cmd_log.append({"t": time.time(), "body": body})
        config["cmd_log"] = cmd_log[-30:]
        # anchor the session start when turning ON so boundary maths use the
        # gateway's clock (+lag), not the next poll time
        if body.get("power") == "1":
            code = ((self.account_state or {}).get("aircon") or {}).get("aircon_code")
            if code:
                sessions = config.setdefault("session_start", {})
                sessions[code] = time.time() + float((self.model or {}).get("lag") or 6.0)
                config["session_start"] = sessions
        api.save_config(config)

        def ok(_r):
            self.busy = False
            self.footer.configure(text=success_note or "Done — updating…")
            self.after(3000, self.refresh)

        def err(e):
            self.busy = False
            self.footer.configure(text=e)

        self._run(lambda: api.api_request("POST", "v2/ac/control", token=token, body=body),
                  on_ok=ok, on_err=err)

    def toggle_power(self):
        if self.smart_enabled:
            self._set_smart(False)
        a = (self.account_state or {}).get("aircon") or {}
        target = "0" if a.get("power") else "1"
        self._send({"power": target})

    def step_temp(self, delta):
        if self.smart_enabled:
            self._set_smart(False)
        a = (self.account_state or {}).get("aircon") or {}
        cur = a.get("setpoint")
        if cur is None:
            return
        new = max(self.temp_min, min(self.temp_max, int(cur) + delta))
        if new != cur:
            self._send({"setpoint": str(new)})

    def set_fan(self, level):
        if self.smart_enabled:
            self._set_smart(False)
        self._send({"fanstep": level}, success_note=f"Fan set to {level}")

    def toggle_swing(self):
        if self.smart_enabled:
            self._set_smart(False)
        value = "S" if self.swing_switch.get() else "N"
        self._send({"flap": value}, success_note=f"Swing {'on' if value == 'S' else 'off'}")

    # -------------------------------------------------------------- polling
    def _schedule_poll(self):
        self.after(self._poll_interval_ms(), self._poll_tick)

    def _poll_interval_ms(self):
        # once per minute — rate-limit friendly
        return 60000

    def _poll_tick(self):
        self.tick += 1
        if self.main_frame.winfo_ismapped() and api.get_token():
            fetch = False
            if self.calib.get("phase"):
                fetch = True                                    # calibration always samples
            elif self.smart_enabled and self.model:
                fetch = self._smart_needs_poll()                # sparse, model-driven
            else:
                fetch = True                                    # normal mode: 1/min
            if fetch:
                self._last_fetch_ts = time.time()
                self.refresh()
            if self.tick % HISTORY_EVERY == 0:                  # every ~6 min
                self._refresh_lists()
        self._schedule_poll()

    def _refresh_lists(self):
        token = api.get_token()
        if not token:
            return
        self._run(
            lambda: self._fetch_lists(token),
            on_ok=lambda t: self._cache_lists(*t),
            on_err=lambda _e: None,
        )

    def _cache_lists(self, usage, topups, inbox):
        self.usage_cache = usage.get("data") or []
        self.topup_cache = topups.get("data") or []
        self.inbox_cache = inbox.get("data") or []
        self._refine_lag(self.usage_cache)
        self._render_cached_lists()


def smoke_test():
    """Run the real startup/event-loop path offline, including in frozen builds."""
    import tempfile
    from pathlib import Path

    original_path, original_token, original_request = api.CONFIG_PATH, api.get_token, api.api_request

    def reject_network(*args, **kwargs):
        raise AssertionError("Startup smoke test must stay offline")

    with tempfile.TemporaryDirectory() as directory:
        api.CONFIG_PATH = str(Path(directory) / "config.json")
        api.get_token = lambda: None
        api.api_request = reject_network
        app = None
        try:
            app = App()
            app.withdraw()
            errors = []
            app.report_callback_exception = lambda *error: errors.append(error)

            def check_startup():
                assert app.login_frame.winfo_exists()
                assert __version__ in app.title()
                assert callable(app.state), "Application data must not shadow Tk.state()"
                app.state()
                # Receiving account data must also leave the Tk method intact.
                app.account_state = {"aircon": {}}
                app.state()
                from gui_layout_check import check_layout
                check_layout(app)

            app.after(150, check_startup)
            app.after(300, app.quit)
            app.mainloop()
            if errors:
                raise errors[0][1]
        finally:
            if app is not None:
                app.destroy()
            api.CONFIG_PATH, api.get_token, api.api_request = original_path, original_token, original_request


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test-report":
        import json
        from pathlib import Path

        report = Path(sys.argv[2])
        try:
            smoke_test()
        except Exception as error:
            report.write_text(json.dumps({"ok": False, "error": str(error)}), encoding="utf-8")
            raise SystemExit(1)
        report.write_text(json.dumps({"ok": True, "version": __version__}), encoding="utf-8")
        return
    App().mainloop()


if __name__ == "__main__":
    main()
