"""Build standalone CLI + GUI executables on their native operating system."""

import argparse
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True,
                        choices=["windows-x64", "macos-arm64", "linux-x64"])
    args = parser.parse_args()
    release = ROOT / "release" / "binaries"
    release.mkdir(parents=True, exist_ok=True)
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
              "--onefile", "--add-data", "LICENSE:."]
    for name, script, options in (
        ("hall-aircon", "hall_aircon.py", ["--console"]),
        ("HallAircon", "gui.py", ["--windowed", "--collect-all", "customtkinter"]),
    ):
        subprocess.run(common + options + ["--name", name, script], cwd=ROOT, check=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    cli = ROOT / "dist" / f"hall-aircon{suffix}"
    subprocess.run([str(cli), "--version"], check=True)
    subprocess.run([str(cli), "--help"], check=True, stdout=subprocess.DEVNULL)
    # Test the actual windowed executable, not just Python imports or widgets.
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "startup.json"
        command = [str(ROOT / "dist" / f"HallAircon{suffix}"),
                   "--smoke-test-report", str(report)]
        if sys.platform.startswith("linux"):
            command = ["xvfb-run", "-a", *command]
        subprocess.run(command, check=True, timeout=45)
        result = json.loads(report.read_text(encoding="utf-8"))
        if result.get("ok") is not True:
            raise RuntimeError(f"Packaged GUI startup failed: {result}")
        print(f"Packaged GUI startup passed: {result}")
    for name in ("hall-aircon", "HallAircon"):
        destination = release / f"{name}-{args.platform}{suffix}"
        shutil.copy2(ROOT / "dist" / f"{name}{suffix}", destination)
        print(f"Built {destination}")


if __name__ == "__main__":
    main()
