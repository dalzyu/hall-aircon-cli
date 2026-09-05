#!/usr/bin/env python3
"""bangbang.py — thermostat-style bang-bang controller for the Hall Aircon.

The service bills per minute of powered-on time. This controller emulates a
thermostat with a deadband:

    room ≥ HIGH  → turn the aircon ON  (setpoint set to the coldest allowed)
    room ≤ LOW   → turn the aircon OFF

so the meter only runs while the room actually needs cooling. Minimum on/off
times protect the compressor from short-cycling.

Usage:
    python bangbang.py --low 23 --high 25
    python bangbang.py --dry-run          # print decisions without acting
"""

import argparse
import signal
import sys
import time

import hall_aircon_api as api

DEFAULT_POLL = 10          # seconds between temperature reads
MIN_ON_DEFAULT = 180       # seconds the unit must stay on before it may turn off
MIN_OFF_DEFAULT = 240      # seconds the unit must stay off before it may turn on


class Controller:
    def __init__(self, args):
        self.low = args.low
        self.high = args.high
        self.setpoint = str(args.setpoint)
        self.min_on = args.min_on * 60
        self.min_off = args.min_off * 60
        self.poll = args.poll
        self.dry_run = args.dry_run
        self.on_exit = args.on_exit

        self.on_since = None
        self.off_since = time.time()   # assume off at start until told otherwise
        self.on_seconds = 0.0
        self.started = time.time()
        self.rate = args.rate

    # ------------------------------------------------------------- helpers
    def fetch_state(self):
        token = api.get_token()
        if not token:
            raise api.ApiError(0, "no token — log in first")
        r = api.api_request("GET", "me", token=token)
        a = (r.get("data") or {}).get("aircon") or {}
        return a

    def send(self, body):
        if self.dry_run:
            return
        token = api.get_token()
        api.api_request("POST", "v2/ac/control", token=token, body=body)

    def log(self, msg):
        print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)

    # ------------------------------------------------------------- cycle
    def step(self):
        a = self.fetch_state()
        temp = a.get("current_temperature")
        power = bool(a.get("power"))
        online = a.get("comm_stat")
        maintenance = a.get("maintenance_mode")

        if maintenance:
            self.log("unit in maintenance — waiting")
            return
        if not online:
            self.log("unit offline — waiting")
            return
        if temp is None:
            self.log("no temperature reported yet")
            return

        now = time.time()
        decision = None

        if power:
            if self.on_since is None:
                self.on_since = now
            if temp <= self.low and now - self.on_since >= self.min_on:
                decision = ("off", f"room {temp}°C ≤ {self.low}°C, cooling done")
            elif now - self.on_since < self.min_on:
                remaining = int(self.min_on - (now - self.on_since))
                self.log(f"room {temp}°C — min-on time, {remaining}s to go")
        else:
            if self.off_since is None:
                self.off_since = now
            if temp >= self.high and now - self.off_since >= self.min_off:
                decision = ("on", f"room {temp}°C ≥ {self.high}°C, start cooling")
            elif now - self.off_since < self.min_off:
                remaining = int(self.min_off - (now - self.off_since))
                self.log(f"room {temp}°C — min-off time, {remaining}s to go")

        if decision is None:
            return

        action, reason = decision
        self.log(reason + f"  →  turning {action.upper()}" + (" (dry-run)" if self.dry_run else ""))
        if action == "on":
            # set coldest setpoint + power on (two commands, order doesn't matter much)
            self.send({"setpoint": self.setpoint})
            self.send({"power": "1"})
            self.on_since = time.time()
            self.off_since = None
        else:
            self.send({"power": "0"})
            self.off_since = time.time()
            if self.on_since is not None:
                self.on_seconds += time.time() - self.on_since
            self.on_since = None

    def run(self):
        self.log(f"bang-bang controller: ON ≥ {self.high}°C, OFF ≤ {self.low}°C, "
                 f"cooling setpoint {self.setpoint}°C, min-on {self.min_on // 60}m, "
                 f"min-off {self.min_off // 60}m" + ("  [DRY-RUN]" if self.dry_run else ""))

        def stop(_sig, _frame):
            self.log("stopping")
            if self.on_exit is not None and not self.dry_run:
                self.send({"power": self.on_exit})
                self.log(f"set power={self.on_exit} on exit")
            self._report()
            sys.exit(0)

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        while True:
            try:
                self.step()
                if self.on_since is not None and time.time() - self.on_since > 3600:
                    # periodic status while running long
                    self.log(f"running — est. cost so far SGD {self.on_seconds * self.rate:.2f}")
            except api.ApiError as e:
                self.log(f"api error: {e} (retrying)")
            except Exception as e:  # noqa: BLE001
                self.log(f"error: {e}")
            time.sleep(self.poll)

    def _report(self):
        minutes = self.on_seconds / 60
        elapsed = (time.time() - self.started) / 60
        self.log(f"summary: on {minutes:.1f} min · est. SGD {minutes * self.rate:.2f} "
                 f"over {elapsed:.1f} min (duty {100 * minutes / max(elapsed, 1):.0f}%)")


def main():
    p = argparse.ArgumentParser(description="Experimental thermostat controller for Hall Aircon")
    p.add_argument("--low", type=int, default=23, help="turn OFF when room reaches this (default 23)")
    p.add_argument("--high", type=int, default=25, help="turn ON when room reaches this (default 25)")
    p.add_argument("--setpoint", type=int, default=22, help="cooling setpoint while ON (default 22, server floor)")
    p.add_argument("--min-on", type=int, default=3, help="minimum ON time in minutes (compressor protection, default 3)")
    p.add_argument("--min-off", type=int, default=4, help="minimum OFF time in minutes (compressor protection, default 4)")
    p.add_argument("--poll", type=int, default=DEFAULT_POLL, help=f"poll interval seconds (default {DEFAULT_POLL})")
    p.add_argument("--rate", type=float, default=api.load_config().get("rate") or 0.0065,
                   help="SGD per minute for estimates (auto-reads from history, default 0.0065)")
    p.add_argument("--on-exit", choices=["0", "1"], default=None,
                   help="leave the unit OFF (0) or ON (1) when the controller stops")
    p.add_argument("--dry-run", action="store_true", help="print decisions without sending commands")
    args = p.parse_args()

    if args.low >= args.high:
        p.error("--low must be below --high")
    if not 16 <= args.setpoint <= 30:
        p.error("--setpoint must be between 16 and 30")
    if args.poll <= 0 or args.min_on <= 0 or args.min_off <= 0:
        p.error("--poll, --min-on and --min-off must be positive")
    if args.rate < 0:
        p.error("--rate must be non-negative")
    Controller(args).run()


if __name__ == "__main__":
    main()
