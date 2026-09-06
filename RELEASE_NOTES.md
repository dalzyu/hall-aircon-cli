## Hall Aircon v1.5.2

Fixes desktop controls being clipped at shorter window heights.

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

- Control and History content now scroll when the window is too short.
- Log out and the status footer retain their own space outside the scrolling tabs.
- The minimum window height is now 420 logical pixels; long header and status
  text wraps within the supported minimum width.
- History uses one scrollbar for both usage and top-ups, avoiding nested scrolling.
- Sign-in also scrolls so the SSO completion button remains reachable in short windows.
- Packaged GUI checks verify content reachability and footer visibility at four
  window heights and two UI scaling levels, as well as the startup regression.
- Python and application dependencies remain bundled in each executable.

### Experimental features and validation limits

GUI Smart mode, thermal calibration, and the source-installed standalone
thermostat remain **experimental**. They can switch your aircon and affect
billed usage. Savings and minute-boundary timing are estimates, not guarantees.
The standalone thermostat has minimum on/off timers; GUI Smart mode does not
provide equivalent compressor-cycle protection. Keep automated operation supervised.
Closing the application stops its automation but does not necessarily turn off
the physical unit. Use the official app to confirm its state when needed.

Validation uses simulated API responses, source GUI event-loop checks, and packaged
CLI and GUI startup checks. Real NTU SSO, current service compatibility, physical-unit
behavior, and unsigned-binary first-run behavior on every OS were not validated
for this release.
