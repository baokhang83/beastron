import json
import os
from pathlib import Path

DEFAULTS = json.loads((Path(__file__).parent / "config" / "defaults.json").read_text())

# The environment can override the defaults.
REQUEST_TIMEOUT = float(os.environ.get("SHOP_REQUEST_TIMEOUT", DEFAULTS["http"]["timeout_seconds"]))
