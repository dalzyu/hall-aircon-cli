#!/usr/bin/env python3
"""gui.py — cross-platform desktop GUI for the Hall Aircon service.

Runs on Windows, macOS and Linux. Requires: customtkinter
    pip install customtkinter
"""

import threading
import webbrowser
from datetime import datetime

import customtkinter as ctk

import hall_aircon_api as api

POLL_SECONDS = 10
GREEN = "#2E7D32"
GRAY = "#5A5A5A"
RED = "#C62828"
AMBER = "#F9A825"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Hall Aircon")
        self.geometry("430x720")
        self.minsize(400, 640)

        self.state = None            # latest /me data
        self.temp_min, self.temp_max = 16, 30
        self.busy = False
        self.logged_in_email = None
        self._build_login()
        self._build_main()

        self.show_login()
        token = api.get_token()
        if token:
            self.load_main()
        self._schedule_poll()

    # ------------------------------------------------------------------ UI
    def _build_login(self):
        self.login_frame = ctk.CTkFrame(self)
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(self.login_frame, text="Hall Aircon", font=ctk.CTkFont(size=28, weight="bold")).pack(pady=(28, 4))
        ctk.CTkLabel(self.login_frame, text="Sign in with your account", font=ctk.CTkFont(size=14)).pack(pady=(0, 20))

        self.email_entry = ctk.CTkEntry(self.login_frame, width=300, placeholder_text="you@e.ntu.edu.sg")
        self.email_entry.pack(pady=6)

        # password pane (non-student accounts)
        self.password_entry = ctk.CTkEntry(self.login_frame, width=300, placeholder_text="Password", show="*")

        # SSO pane (student accounts)
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
        self.header.pack(pady=(24, 0))
        self.sub_header = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=13), text_color="#9e9e9e")
        self.sub_header.pack(pady=(0, 8))

        self.balance_label = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=15))
        self.balance_label.pack(pady=(0, 10))

        self.warn_label = ctk.CTkLabel(self.main_frame, text="", text_color=AMBER, font=ctk.CTkFont(size=13), wraplength=360)
        self.warn_label.pack(pady=(0, 6))

        self.power_btn = ctk.CTkButton(self.main_frame, width=280, height=84, corner_radius=16,
                                       font=ctk.CTkFont(size=26, weight="bold"),
                                       command=self.toggle_power)
        self.power_btn.pack(pady=(6, 18))

        temp_row = ctk.CTkFrame(self.main_frame, fg_color="transparent")
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

        self.current_label = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=13), text_color="#9e9e9e")
        self.current_label.pack(pady=(2, 12))

        ctk.CTkLabel(self.main_frame, text="Fan speed", font=ctk.CTkFont(size=13)).pack()
        fan_row = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        fan_row.pack(pady=4)
        self.fan_btns = {}
        for i, level in enumerate(api.FAN_LEVELS):
            label = "Auto" if level == "A" else str(i)
            btn = ctk.CTkButton(fan_row, text=label, width=52, height=34,
                                command=lambda lv=level: self.set_fan(lv))
            btn.pack(side="left", padx=3)
            self.fan_btns[level] = btn

        ctk.CTkLabel(self.main_frame, text="(Auto, 1 = low … 5 = high)", font=ctk.CTkFont(size=11), text_color="#757575").pack(pady=(0, 12))

        self.swing_switch = ctk.CTkSwitch(self.main_frame, text="Swing (louver)", font=ctk.CTkFont(size=14),
                                          command=self.toggle_swing)
        self.swing_switch.pack(pady=4)
        ctk.CTkLabel(self.main_frame, text="Fan & swing work only on units that support them.",
                     font=ctk.CTkFont(size=11), text_color="#757575").pack(pady=(0, 14))

        self.footer = ctk.CTkLabel(self.main_frame, text="", font=ctk.CTkFont(size=12), text_color="#9e9e9e")
        self.footer.pack(side="bottom", pady=(0, 8))

        self.logout_btn = ctk.CTkButton(self.main_frame, text="Log out", width=100, fg_color="transparent",
                                        border_width=1, command=self.do_logout)
        self.logout_btn.pack(side="bottom", pady=(0, 14))

    # -------------------------------------------------------------- screen mgmt
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

    # -------------------------------------------------------------- threading
    def _run(self, fn, on_ok=None, on_err=None):
        def worker():
            try:
                result = fn()
            except api.ApiError as e:
                self.after(0, lambda: on_err(str(e)) if on_err else None)
                return
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: on_err(str(e)) if on_err else None)
                return
            self.after(0, lambda: on_ok(result) if on_ok else None)
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
            # student account → NTU SSO in browser
            self.sso_url = data.get("login_url") or api.SAML_PREFIX
            self.sso_pane.pack(pady=4)
            self.back_btn.pack(pady=4)
            self._set_login_error("")
        else:
            # non-student → password
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

    # -------------------------------------------------------------- main screen
    def load_main(self):
        self.show_main()
        self.refresh(initial=True)

    def refresh(self, initial=False):
        token = api.get_token()
        if not token:
            return
        self._run(
            lambda: self._fetch_all(token),
            on_ok=lambda data: self._render(data),
            on_err=lambda e: self._render_error(e, initial),
        )

    def _fetch_all(self, token):
        me = api.api_request("GET", "me", token=token)
        try:
            lo = api.api_request("GET", "app-config/temperature_allowed_min", token=token)
            self.temp_min = int(lo["data"]["value"])
        except (api.ApiError, KeyError, TypeError, ValueError):
            pass
        try:
            hi = api.api_request("GET", "app-config/temperature_allowed_max", token=token)
            self.temp_max = int(hi["data"]["value"])
        except (api.ApiError, KeyError, TypeError, ValueError):
            pass
        return me

    def _render(self, me):
        data = me.get("data") or {}
        self.state = data
        a = data.get("aircon") or {}

        room = a.get("room_name") or a.get("aircon_code") or ""
        hall = (a.get("hall") or {}).get("hall_name", "")
        self.header.configure(text=f"{hall} · {room}" if hall else room or "No aircon paired")
        self.balance_label.configure(text=f"Balance: SGD {data.get('balance', '—')}")

        if not a:
            self.sub_header.configure(text="Pair an aircon in the official app first")
            self.warn_label.configure(text="")
            self._set_controls_enabled(False)
        elif a.get("maintenance_mode"):
            self.warn_label.configure(text="⚠ The aircon is under maintenance")
            self.sub_header.configure(text="")
            self._set_controls_enabled(False)
        elif not a.get("comm_stat"):
            self.warn_label.configure(text="⚠ Aircon is offline — commands may not reach it")
            self.sub_header.configure(text="")
            self._set_controls_enabled(False)
        else:
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
        mode = a.get("mode")
        self.current_label.configure(
            text=(f"Room: {cur}°C" if cur is not None else "Room: —") +
                 (f"  ·  mode {mode}" if mode else "")
        )
        self.temp_down.configure(state="normal" if power_on and setpoint and setpoint > self.temp_min else "disabled")
        self.temp_up.configure(state="normal" if power_on and setpoint is not None and setpoint < self.temp_max else "disabled")

        fanstep = a.get("fanstep")
        for level, btn in self.fan_btns.items():
            btn.configure(fg_color=("#1F6AA5" if level == fanstep else "#3B3B3B"),
                          hover_color=("#1F6AA5" if level == fanstep else "#4a4a4a"))
        flap = a.get("flap")
        if flap in ("S", "N"):
            self.swing_switch.select() if flap == "S" else self.swing_switch.deselect()

        self.footer.configure(text=f"Updated {datetime.now().strftime('%H:%M:%S')}")

    def _render_error(self, message, initial):
        self.footer.configure(text=message)
        if initial:
            self.header.configure(text="Could not load data")
            self.sub_header.configure(text=message)

    def _set_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.power_btn.configure(state=state)
        for btn in self.fan_btns.values():
            btn.configure(state=state)
        if enabled:
            self.swing_switch.configure(state="normal")
        else:
            self.swing_switch.configure(state="disabled")

    # -------------------------------------------------------------- actions
    def _send(self, body, success_note=""):
        if self.busy:
            return
        self.busy = True
        self.footer.configure(text="Sending…")
        token = api.get_token()

        def work():
            return api.api_request("POST", "v2/ac/control", token=token, body=body)

        def ok(_r):
            self.busy = False
            self.footer.configure(text=success_note or "Done — updating…")
            self.after(3000, self.refresh)

        def err(e):
            self.busy = False
            self.footer.configure(text=e)

        self._run(work, on_ok=ok, on_err=err)

    def toggle_power(self):
        a = (self.state or {}).get("aircon") or {}
        target = "0" if a.get("power") else "1"
        self._send({"power": target})

    def step_temp(self, delta):
        a = (self.state or {}).get("aircon") or {}
        cur = a.get("setpoint")
        if cur is None:
            return
        new = max(self.temp_min, min(self.temp_max, int(cur) + delta))
        if new != cur:
            self._send({"setpoint": str(new)})

    def set_fan(self, level):
        self._send({"fanstep": level}, success_note=f"Fan set to {level} (if your unit supports it)")

    def toggle_swing(self):
        value = "S" if self.swing_switch.get() else "N"
        self._send({"flap": value}, success_note=f"Swing {'on' if value == 'S' else 'off'} (if your unit supports it)")

    # -------------------------------------------------------------- polling
    def _schedule_poll(self):
        self.after(POLL_SECONDS * 1000, self._poll_tick)

    def _poll_tick(self):
        if self.main_frame.winfo_ismapped() and api.get_token():
            self.refresh()
        self._schedule_poll()


if __name__ == "__main__":
    App().mainloop()
