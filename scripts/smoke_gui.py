"""Construct the login UI with an isolated config and no network calls."""

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hall_aircon_api as api
import gui
from hall_aircon_version import __version__

with tempfile.TemporaryDirectory() as directory, \
     patch.object(api, "CONFIG_PATH", str(Path(directory) / "config.json")), \
     patch.object(api, "get_token", return_value=None), \
     patch.object(api, "api_request", side_effect=AssertionError("GUI smoke check must stay offline")):
    app = gui.App()
    app.withdraw()
    app.update_idletasks()
    assert app.login_frame.winfo_exists()
    assert __version__ in app.title()
    app.destroy()
print("GUI login screen constructed successfully (offline)")
