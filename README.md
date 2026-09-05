# Hall Aircon

[![Tests](https://github.com/dalzyu/hall-aircon-cli/actions/workflows/test.yml/badge.svg)](https://github.com/dalzyu/hall-aircon-cli/actions/workflows/test.yml)

Unofficial command-line and desktop clients for the Hall Aircon service.
Use your own account to view balance, usage, top-ups, notifications, and control
your aircon. Not affiliated with Daikin or NTU.

## Download

Get an executable from the [latest release](https://github.com/dalzyu/hall-aircon-cli/releases/latest).
Each is a single file containing Python and its application dependencies.
No Python installation, pip install, or archive extraction is needed.

| Platform | Desktop app | CLI |
| --- | --- | --- |
| Windows x64 | `HallAircon-windows-x64.exe` | `hall-aircon-windows-x64.exe` |
| macOS 14+ Apple Silicon | `HallAircon-macos-arm64` | `hall-aircon-macos-arm64` |
| Linux x64 (Ubuntu 22.04+ / glibc 2.35+) | `HallAircon-linux-x64` | `hall-aircon-linux-x64` |

On Windows, double-click the desktop app. On macOS/Linux,
run `chmod +x FILENAME`, then `./FILENAME` from a terminal.
Linux needs a graphical desktop for the GUI. Intel Mac users can run from source.
Standard operating-system libraries are still required on each platform.
These are unsigned binaries; macOS builds are not notarized. Your operating
system may require explicit approval to open the downloaded app. Verify the
download source before approving it; do not disable security tools.

## Install from source

Requires Python 3.10 or newer. The CLI uses only the standard library.
The GUI adds CustomTkinter and requires Python's Tk support (on Ubuntu,
install `python3-tk`).

```bash
git clone https://github.com/dalzyu/hall-aircon-cli.git
cd hall-aircon-cli
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead: .\.venv\Scripts\Activate.ps1
python -m pip install '.[gui]'
hall-aircon-gui
```

For CLI only, use `python -m pip install .`.
From a checkout, `python hall_aircon.py --help` runs without installation.

## Command-line usage

The examples use the installed `hall-aircon` command. For downloaded binaries,
use the downloaded filename, such as `./hall-aircon-linux-x64` on Linux or
`.\hall-aircon-windows-x64.exe` in PowerShell.

```bash
hall-aircon --version
hall-aircon login --email YOU0001@e.ntu.edu.sg
# Non-student account: password is prompted without echoing it.
hall-aircon login --email you@example.com
hall-aircon status
hall-aircon on
hall-aircon off
hall-aircon temp 23
hall-aircon fan M       # A, L, LM, M, MH, H (unit-dependent)
hall-aircon swing on    # on | off (unit-dependent)
hall-aircon usage --limit 20 --offset 0
hall-aircon topups
hall-aircon inbox
hall-aircon logout
```

Student login provides an NTU SSO link. Sign in through your browser, then paste
the final URL beginning with
`https://cmsntu-prod.daikinpayu.com/adfs/saml/redirect/` into the client.
Your NTU password stays in the browser. For non-SSO accounts, the password is
sent to the service's login endpoint and is not saved locally.

Session tokens are saved in `~/.config/hall-aircon/config.json`. Writes are
atomic and use private file permissions on POSIX; Windows access is governed
by your user profile's filesystem permissions. Logout removes the saved token
while retaining preferences. Treat tokens and SSO redirect URLs as credentials.

| Environment variable | Purpose |
| --- | --- |
| `HALL_AIRCON_TOKEN` | Use a token without logging in; overrides the saved token |
| `HALL_AIRCON_CONFIG_DIR` | Override the configuration directory |
| `HALL_AIRCON_API` | Override the API base URL; credentials go to this destination |
| `HALL_AIRCON_USER_AGENT` | Override the service-compatible User-Agent |

`--token` overrides both environment and saved tokens. Passwords and tokens
passed on the command line may remain in shell history; prefer interactive
login. Logout cannot remove a token from your shell environment; unset it there.

## Desktop app

- **Control:** power, setpoint, supported fan/swing controls, room temperature,
  balance, online/maintenance state, and estimated session cost.
- **History:** usage sessions and top-ups.
- **Inbox:** service notifications.

Fan and swing controls are disabled when the unit reports no corresponding
state. API acceptance alone does not establish hardware support. Normal GUI
polling is once per minute; commands trigger a follow-up refresh.

## Experimental thermostat features

**Smart mode and calibration are experimental.** The GUI uses a fitted thermal
model to predict switching times. It cools with an internal 22 °C setpoint,
switches off at the lower room-temperature threshold, and restarts at the upper
threshold. The default band is target ±1 °C; target changes shift the whole
band. Saved `smart.off_at` / `smart.on_at` values can define an asymmetric band.
The GUI target range is 23–26 °C.

Press **Calibrate** and follow the setup instructions to fit the model before
enabling Smart. Smart checks near predicted switches and at least every
15 minutes. Shutdowns attempt to align with billing-minute boundaries using
estimated gateway lag. Savings and timing are estimates, not guarantees.
Manual controls disable Smart and cancel its pending shutdown.

An independent, simpler thermostat is available after source installation:

```bash
hall-aircon-thermostat --dry-run --poll 60
hall-aircon-thermostat --low 23 --high 25 --poll 60
```

`--dry-run` still reads the API but sends no control commands. The standalone
controller defaults to 3-minute minimum on and 4-minute minimum off intervals;
GUI Smart mode does not provide equivalent compressor-cycle protection.
Keep automated operation supervised. The standalone controller defaults to
leaving power unchanged on exit; use `--on-exit 0` to request shutdown.

Keep the app running for automation and calibration to work. Closing it stops
automation but does not necessarily turn off the unit. Turning the unit off
settles the current billed session. Commands may take several seconds to reach
the physical unit. Avoid rapid toggling and respect service rate limits.

## Development and releases

```bash
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python scripts/smoke_gui.py
python -m build
python scripts/build_release.py --platform windows-x64
```

Build on the target OS, using `macos-arm64` or `linux-x64` as appropriate.
On headless Linux, use `xvfb-run -a python scripts/smoke_gui.py`.
Source tests run on Python 3.10, 3.12, and 3.14. Release binaries use Python 3.12.
Update `hall_aircon_version.py` and `RELEASE_NOTES.md`, validate CI, then push a
matching `v*` tag. All three platform builds must pass before the workflow
publishes the standalone executables. Manual workflow
runs produce downloadable build artifacts without publishing a release.

Tests use simulated responses and offline GUI construction. NTU SSO, live
service compatibility, physical hardware, and platform first-run approval
flows were not validated for v1.5.0. See [release notes](RELEASE_NOTES.md).

## Contributing and security

Report bugs with your OS, client version, reproduction steps, and redacted
errors. Never attach tokens, passwords, configuration files, or SSO redirects.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

MIT licensed; see [LICENSE](LICENSE).
