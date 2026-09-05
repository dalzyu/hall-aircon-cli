"""Build portable CLI + GUI archives on their native operating system."""

import argparse
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True,
                        choices=["windows-x64", "macos-arm64", "linux-x64"])
    args = parser.parse_args()
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
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
    archive = release / f"HallAircon-{args.platform}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in (cli, ROOT / "dist" / f"HallAircon{suffix}",
                     ROOT / "LICENSE", ROOT / "README.md", ROOT / "RELEASE_NOTES.md"):
            output.write(path, path.name)
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
