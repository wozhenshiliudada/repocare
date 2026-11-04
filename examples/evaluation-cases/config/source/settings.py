import json
from pathlib import Path

def load_limit():
    value = json.loads(Path(__file__).with_name("settings.json").read_text())["max_items"]
    if not isinstance(value, int) or value < 1:
        raise ValueError("max_items must be a positive integer")
    return value
