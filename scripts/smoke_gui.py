"""Exercise GUI startup and its event loop with an isolated, offline config."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui

gui.smoke_test()
print("GUI startup and event loop passed (offline)")
