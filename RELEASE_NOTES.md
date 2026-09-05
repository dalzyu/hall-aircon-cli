## Hall Aircon v1.5.0

Public release of the unofficial Hall Aircon CLI and desktop app.

### Downloads

Each platform ZIP includes the desktop app, a standalone `hall-aircon` CLI,
the README, release notes, and MIT license:

- `HallAircon-windows-x64.zip`: Windows x64.
- `HallAircon-macos-arm64.zip`: Apple Silicon, macOS 14 or newer.
- `HallAircon-linux-x64.zip`: Linux x64, glibc 2.35 or newer (Ubuntu 22.04+).

Python wheel and source distributions are also provided. Python 3.10+ is
required only when running from source or installing the wheel.
`SHA256SUMS.txt` contains checksums for all release downloads.
The binaries are unsigned and the macOS binary is not notarized.

### Changes

- Added installable Python packaging and `--version`.
- Added standalone CLI binaries alongside the desktop app on all three platforms.
- Added offline regression tests and GUI construction checks.
- Build all platforms before publishing a complete release, with checksums.
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
