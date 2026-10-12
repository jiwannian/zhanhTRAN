"""Read a report that another process may still be writing."""
import json


def read_report(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return None
