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

POLL_SECONDS = 10
HISTORY_EVERY = 6          # fetch history/inbox every 6th poll (~1 min)
PROBE_DEADLINE = 40        # seconds to wait for a capability probe answer
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

        self.title("Hall Aircon")
        self.geometry("440x760")
        self.minsize(410, 660)

        self.state = None               # latest /me data
        self.temp_min, self.temp_max = 16, 30
        self.config_loaded = False
        self.busy = False
        self.tick = 0
        self.logged_in_email = None
        self.usage_cache, self.topup_cache, self.inbox_cache = [], [], []
        self.probing = False
        self.probe_deadline = 0.0
        self._ui_queue = queue.Queue()  # thread -> main-thread callback queue

        # smart (bang-bang) mode state
        sc = api.load_config().get("smart") or {}
        self.smart_enabled = bool(sc.get("enabled"))
        self.smart_target = max(23, min(26, int(sc.get("target", 24))))
        st = api.load_config().get("smart_stats") or {"date": "", "on_s": 0, "win_s": 0}
        self._smart_stats = st
        self._smart_last_ts = time.time()
        self._smart_last_save = time.time()

        self._build_login()
        self._build_main()
        self.smart_target_lbl.configure(text=f"{self.smart_target}°C")
        if self.smart_enabled:
            self.smart_switch.select()
            self.smart_status.configure(
                text=f"Smart ON — cools to {self.smart_target - 1}°C, then off until {self.smart_target + 1}°C")

        self.show_login()
        if api.get_token():
            self.load_main()
        self._schedule_poll()
        self.after(100, self._drain_queue)

    def _drain_queue(self):
        """Run UI callbacks posted from worker threads (thread-safe)."""
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ UI
    def _build_login(self):
        self.login_frame = ctk.CTkFrame(self)
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")

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

        self.header = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=20, weight="bold"))
        self.header.pack(pady=(20, 0))
        self.sub_header = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=13), text_color="#9e9e9e")
        self.sub_header.pack(pady=(0, 4))
        self.warn_label = ctk.CTkLabel(self.main_frame, text="", text_color=AMBER, font=ctk.CTkFont(size=13), wraplength=390)
        self.warn_label.pack(pady=(0, 2))

        self.tabs = ctk.CTkTabview(self.main_frame)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(4, 4))
        self.tabs.add("Control")
        self.tabs.add("History")
        self.tabs.add("Inbox")
        self._build_control_tab(self.tabs.tab("Control"))
        self._build_history_tab(self.tabs.tab("History"))
        self._build_inbox_tab(self.tabs.tab("Inbox"))

        self.footer = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=12), text_color="#9e9e9e")
        self.footer.pack(side="bottom", pady=(0, 4))
        self.logout_btn = ctk.CTkButton(self.main_frame, text="Log out", width=100, fg_color="transparent",
                                        border_width=1, command=self.do_logout)
        self.logout_btn.pack(side="bottom", pady=(0, 12))

    def _build_control_tab(self, tab):
        self.balance_label = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=15))
        self.balance_label.pack(pady=(10, 4))
        self.session_label = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color="#9e9e9e")
        self.session_label.pack(pady=(0, 6))

        # smart (bang-bang) mode: cool to target-1, restart at target+1
        smart_frame = ctk.CTkFrame(tab)
        smart_frame.pack(pady=(0, 8))
        self.smart_switch = ctk.CTkSwitch(smart_frame, text="Smart (save money)", font=ctk.CTkFont(size=14),
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
        self.smart_status = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color=AMBER, wraplength=380)
        self.smart_status.pack(pady=(0, 4))

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
        self.usage_box = ctk.CTkScrollableFrame(tab, height=180)
        self.usage_box.pack(fill="x", padx=10)

        ctk.CTkLabel(tab, text="Top-ups", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(12, 4))
        self.topup_box = ctk.CTkScrollableFrame(tab, height=120)
        self.topup_box.pack(fill="x", padx=10, pady=(0, 10))

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
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.show_email_step()

    def show_email_step(self):
        self.password_entry.pack_forget()
        self.sso_pane.pack_forget()
        self.back_btn.pack_forget()
        self.login_btn.pack(pady=10)
        self.login_error.configure(text="")

    def show_main(self):
        self.login_frame.place_forget()
        self.main_frame.pack(fill="both", expand=True)

    def _set_login_error(self, message: str):
        self.login_error.configure(text=message)

    # -------------------------------------------------------------- threads
    def _run(self, fn, on_ok=None, on_err=None):
        def worker():
            try:
                result = fn()
            except api.ApiError as e:
                self._ui_queue.put(lambda: on_err(str(e)) if on_err else None)
                return
            except Exception as e:  # noqa: BLE001
                self._ui_queue.put(lambda: on_err(str(e)) if on_err else None)
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
        self._run(
            lambda: api.api_request("POST", "auth/logout", token=api.get_token()),
            on_ok=lambda _r: (api.clear_token(), self._reset_to_login()),
            on_err=lambda _e: (api.clear_token(), self._reset_to_login()),
        )

    def _reset_to_login(self):
        self.state = None
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
        self.state = data
        a = data.get("aircon") or {}
        code = a.get("aircon_code") or "?"

        self._track_session(a)
        self._update_capabilities(a)

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
        self.smart_enabled = enabled
        config = api.load_config()
        config["smart"] = {"enabled": enabled, "target": self.smart_target}
        api.save_config(config)
        if enabled:
            self.smart_switch.select()
            self._smart_stats_reset_if_needed()
            self.smart_status.configure(
                text=f"Smart ON — cools to {self.smart_target - 1}°C, then off until {self.smart_target + 1}°C")
        else:
            self.smart_switch.deselect()
            self.smart_status.configure(text="Smart mode off")
        self._update_smart_label()

    def _smart_target_delta(self, delta):
        # user-facing target range 23..26; smart cools to 22 internally
        new = max(23, min(26, self.smart_target + delta))
        if new == self.smart_target:
            return
        self.smart_target = new
        self.smart_target_lbl.configure(text=f"{new}°C")
        config = api.load_config()
        config["smart"] = {"enabled": self.smart_enabled, "target": new}
        api.save_config(config)
        if self.smart_enabled:
            self.smart_status.configure(
                text=f"Smart ON — cools to {new - 1}°C, then off until {new + 1}°C")

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
        self._update_smart_label()

    def _update_smart_label(self):
        on_s = int(self._smart_stats.get("on_s", 0))
        win_s = max(int(self._smart_stats.get("win_s", 0)), 1)
        duty = 100.0 * on_s / win_s
        saved = (win_s - on_s) / 60.0 * self._session_rate()
        if self.smart_enabled and win_s >= 60:
            self.smart_status.configure(
                text=f"Smart: duty {duty:.0f}% · ≈ SGD {saved:.2f} saved since {self._smart_stats.get('date', '')}"
                     f" — cools to {self.smart_target - 1}°C, restarts at {self.smart_target + 1}°C")

    def _smart_tick(self, a):
        """Bang-bang controller: cycle power to keep the room in [target-1, target+1]."""
        if not self.smart_enabled or self.busy or self.probing:
            return
        t = a.get("current_temperature")
        if t is None or not a.get("comm_stat") or a.get("maintenance_mode"):
            return
        self._smart_stats_reset_if_needed()
        self._smart_stats_accumulate(bool(a.get("power")))

        low = self.smart_target - 1
        high = self.smart_target + 1
        if a.get("power"):
            if t <= low:
                start = (api.load_config().get("session_start") or {}).get(a.get("aircon_code"))
                if start is None or (time.time() - start) % 60 <= 2:
                    # align the shutdown with a whole-minute boundary: billing
                    # rounds UP, so ending at X:00 pays exactly X minutes
                    self._send({"power": "0"}, success_note=f"Smart: cooled to {t}°C, turning off")
        else:
            if t >= high:
                self._send({"power": "1", "setpoint": "22"},
                           success_note=f"Smart: warmed to {t}°C, turning on (setpoint 22)")

    # -------------------------------------------------------------- capability probe
    def _caps(self, code):
        return (api.load_config().get("capabilities") or {}).get(code, {})

    def _set_cap(self, code, key, value):
        config = api.load_config()
        caps = config.setdefault("capabilities", {})
        caps.setdefault(code, {})[key] = value
        api.save_config(config)

    def _update_capabilities(self, a):
        code = a.get("aircon_code")
        if not code:
            return
        caps = self._caps(code)

        # values reported by the unit → supported
        if a.get("fanstep"):
            caps["fan"] = True
        if a.get("flap"):
            caps["swing"] = True
        if "fan" in caps and "swing" in caps:
            self.probing = False
            self._set_cap(code, "fan", caps["fan"])
            self._set_cap(code, "swing", caps["swing"])

        self._apply_cap_ui(code, caps)

        # run a one-time probe for unknown capabilities while the unit is on
        if (not self.probing and a.get("power") and a.get("comm_stat")
                and ("fan" not in caps or "swing" not in caps)):
            self.probing = True
            self.probe_deadline = time.time() + PROBE_DEADLINE
            body = {}
            if "fan" not in caps:
                body["fanstep"] = "A"
            if "swing" not in caps:
                body["flap"] = "N"
            self.footer.configure(text="Checking which features your unit supports…")
            token = api.get_token()

            def ok(_r):
                pass

            def err(e):
                self.probing = False
                self.footer.configure(text=e)
            self._run(lambda: api.api_request("POST", "v2/ac/control", token=token, body=body), on_ok=ok, on_err=err)

        # deadline expired → mark still-null capabilities as unsupported,
        # but only if the unit stayed on for the whole probe window
        if self.probing and time.time() > self.probe_deadline:
            self.probing = False
            if a.get("power") and a.get("comm_stat"):
                if "fan" not in caps:
                    caps["fan"] = False
                if "swing" not in caps:
                    caps["swing"] = False
                self._set_cap(code, "fan", caps["fan"])
                self._set_cap(code, "swing", caps["swing"])
                self._apply_cap_ui(code, caps)
                self.footer.configure(text=f"Updated {datetime.now().strftime('%H:%M:%S')}")

    def _apply_cap_ui(self, code, caps):
        fan_known = "fan" in caps
        swing_known = "swing" in caps

        if fan_known and caps["fan"]:
            self.fan_hint.configure(text="Auto, 1 = low … 5 = high")
            for btn in self.fan_btns.values():
                btn.configure(state="normal")
        elif fan_known and not caps["fan"]:
            self.fan_hint.configure(text="Fan speed is not supported by this unit", text_color=AMBER)
            for btn in self.fan_btns.values():
                btn.configure(state="disabled")
        else:
            self.fan_hint.configure(text="Checking fan speed support…")
            for btn in self.fan_btns.values():
                btn.configure(state="disabled")

        if swing_known and caps["swing"]:
            self.swing_hint.configure(text="")
            self.swing_switch.configure(state="normal")
        elif swing_known and not caps["swing"]:
            self.swing_hint.configure(text="Swing is not supported by this unit", text_color=AMBER)
            self.swing_switch.configure(state="disabled")
        else:
            self.swing_hint.configure(text="Checking swing support…")
            self.swing_switch.configure(state="disabled")

    def _set_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.power_btn.configure(state=state)
        caps = self._caps((self.state or {}).get("aircon", {}).get("aircon_code"))
        for level, btn in self.fan_btns.items():
            btn.configure(state=state if (enabled and caps.get("fan", False)) else "disabled")
        if enabled and caps.get("swing", False):
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
        a = (self.state or {}).get("aircon") or {}
        target = "0" if a.get("power") else "1"
        self._send({"power": target})

    def step_temp(self, delta):
        if self.smart_enabled:
            self._set_smart(False)
        a = (self.state or {}).get("aircon") or {}
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
        # poll twice as fast while a capability probe is running
        return 5000 if self.probing else POLL_SECONDS * 1000

    def _poll_tick(self):
        self.tick += 1
        if self.main_frame.winfo_ismapped() and api.get_token():
            self.refresh()
            if self.tick % HISTORY_EVERY == 0:
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
        self._render_cached_lists()


if __name__ == "__main__":
    App().mainloop()
