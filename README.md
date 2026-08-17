# hall-aircon-cli

Unofficial desktop and command-line clients for the **Hall Aircon** service.
They talk to the same public HTTPS API that the official mobile app uses, with
**your own** account. The CLI has no third-party dependencies; the GUI needs
one (customtkinter).

> Not affiliated with Daikin or NTU. Use only with your own account and your
> own aircon unit.

## Desktop GUI (Windows / macOS / Linux)

### Option A — download a ready-made binary (no Python needed)

Grab the latest release from the [Releases page](../../releases):

| OS | File |
|---|---|
| Windows | `HallAircon.exe` — double-click to run |
| macOS | `HallAircon-macos` — right-click → Open (unsigned build; see note below) |
| Linux | `HallAircon-linux` — `chmod +x` then run |

macOS note: the binary is not notarized, so Gatekeeper may complain on first
run — right-click the file → Open → Open again, or run `xattr -cr
HallAircon-macos` once. Windows note: some antivirus tools flag single-file
PyInstaller builds; add an exception if needed.

### Option B — run from source

```bash
pip install customtkinter
python gui.py
```

### Blurry UI?

On Windows the app already enables per-monitor DPI awareness. If it still
looks blurry: right-click the .exe → Properties → Compatibility → Change high
DPI settings → tick "Override high DPI scaling behavior" → System (Enhanced).
On Linux, run with integer display scaling (100 % / 200 %) for the sharpest
rendering.

The GUI has three tabs:

- **Control** — big on/off button, temperature, fan speed and swing, balance,
  room temperature, mode, online/maintenance status, and an estimate of the
  running session's cost.
- **History** — usage sessions (minutes + cost, with a daily total) and
  top-ups.
- **Inbox** — service notifications.

Student login: it opens the NTU sign-in page in your browser, you paste the
redirect URL back, done.

**Fan/swing auto-detection**: if the unit never reports `fanstep`/`flap`, the
app probes the hardware once (while the unit is on) and then greys out the
controls with a "not supported by this unit" label — some hall units don't
have these features. The result is remembered per aircon unit.

To build the single-file executables yourself (on each target OS):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --collect-all customtkinter --name HallAircon gui.py
```

Releases are built automatically by
[.github/workflows/build.yml](.github/workflows/build.yml) whenever a `v*`
tag is pushed.

## Command-line tool

```bash
# Log in once (stores the session token in ~/.config/hall-aircon/config.json, mode 0600)
./hall_aircon.py login --email YOU0001@e.ntu.edu.sg

# For non-student (email + password) accounts:
./hall_aircon.py login --email you@example.com --password ...
# Then:
./hall_aircon.py status          # balance + aircon state
./hall_aircon.py on              # turn on
./hall_aircon.py off             # turn off
./hall_aircon.py temp 23         # set setpoint (16-30 C)
./hall_aircon.py fan M           # fan speed: A, L, LM, M, MH, H
./hall_aircon.py swing on        # swing/flap: on | off
./hall_aircon.py usage           # usage history (billed sessions)
./hall_aircon.py topups          # top-up history
./hall_aircon.py inbox           # notifications
./hall_aircon.py logout          # log out and delete the stored token
```

### Token alternatives

You can skip `login` and supply a session token directly:

```bash
export HALL_AIRCON_TOKEN=...
./hall_aircon.py status
# or per-command:
./hall_aircon.py --token ... status
```

## How login works

1. `login` sends your email to the account-check endpoint.
2. **Student accounts** (`@e.ntu.edu.sg`): it prints the NTU sign-in URL. Open it
   in your browser, complete NTU SSO, then paste the final redirect URL (it
   starts with `https://cmsntu-prod.daikinpayu.com/adfs/saml/redirect/`) back
   into the tool. The tool exchanges the redirect for a session token.
3. **Other accounts**: it prompts for your password and logs in directly.

The session token is valid ~90 days and is stored locally with restrictive
permissions. It is never sent anywhere except to the official API.

## Notes

- Turning the aircon **off settles the current usage session** — the accrued
  charge is deducted from your wallet balance. That's normal billing behaviour.
- Commands are accepted by the cloud immediately, but the physical unit may
  take ~10 seconds to reflect a change (`status` shows the last reported state).
- **Fan speed and swing are hardware-dependent**: the API accepts `fan` and
  `swing` commands, but some units don't support them and will never report
  `fanstep`/`flap` (they stay `None` in `status`). If your unit behaves this
  way, those features aren't available on your model.
- The official API enforces rate limits; please don't script rapid toggling.

## License

MIT — see [LICENSE](LICENSE).
