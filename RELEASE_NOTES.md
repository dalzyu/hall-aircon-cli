## Hall Aircon v1.5.0

Public release of the unofficial Hall Aircon CLI and desktop app.

### Downloads

Download the executable for your platform and preferred interface. Each is a
single file with Python and its application dependencies bundled inside;
no Python installation, pip install, or ZIP extraction is needed.

| Platform | Desktop app | CLI |
| --- | --- | --- |
| Windows x64 | `HallAircon-windows-x64.exe` | `hall-aircon-windows-x64.exe` |
| Apple Silicon, macOS 14+ | `HallAircon-macos-arm64` | `hall-aircon-macos-arm64` |
| Linux x64, Ubuntu 22.04+ / glibc 2.35+ | `HallAircon-linux-x64` | `hall-aircon-linux-x64` |

On Windows, double-click the desktop executable. On macOS/Linux, run
`chmod +x FILENAME`, then `./FILENAME`. The GUI needs a graphical desktop.
The executables still rely on their platform's standard operating-system libraries.
The binaries are unsigned and the macOS binary is not notarized.

### Changes

- Added installable Python packaging and `--version`.
- Added standalone CLI binaries alongside the desktop app on all three platforms.
- Added offline regression tests and GUI construction checks.
- Build all platforms before publishing a complete release of standalone binaries.
- Fixed missing password prompts for non-SSO CLI accounts.
- Report API rejections, malformed responses, and timeouts as errors.
- Write configuration atomically, preserving the previous file if a write fails.
- Fixed GUI error callbacks losing their exception messages.
- Cancel scheduled Smart shutdowns when Smart is disabled or the user logs out.
- Fixed elapsed-time handling for model-driven Smart polling.
- Updated installation, authentication, and thermostat documentation.

### Experimental features and validation limits

GUI Smart mode, thermal calibration, and the source-installed standalone
thermostat remain **experimental**. They can switch your aircon and affect
billed usage. Savings and minute-boundary timing are estimates, not guarantees.
The standalone thermostat has minimum on/off timers; GUI Smart mode does not
provide equivalent compressor-cycle protection. Keep automated operation supervised.
Closing the application stops its automation but does not necessarily turn off
the physical unit. Use the official app to confirm its state when needed.

Validation uses simulated API responses, GUI construction checks, and packaged
CLI smoke checks. Real NTU SSO, current service compatibility, physical-unit
behavior, and unsigned-binary first-run behavior on every OS were not validated
for this release.
