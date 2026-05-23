"""Visual assets for AgentEvolver — CSS, templates, and rendering helpers."""

import os

def css_path(filename: str) -> str:
    """Return the absolute path to a CSS file in src/visual/css/."""
    return os.path.join(os.path.dirname(__file__), "css", filename)
