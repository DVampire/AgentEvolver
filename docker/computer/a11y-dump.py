#!/usr/bin/env python3
"""Dump the desktop accessibility (AT-SPI) tree as JSON, for grounding.

Emits a flat list of on-screen, sized elements: ``{role, name, x, y, w, h}`` in
desktop coordinates. The computer environment turns these into Set-of-Marks boxes
so the agent clicks an element by id instead of guessing raw pixels.

Coverage depends on each app's AT-SPI support (GTK/XFCE apps register well;
Chromium needs --force-renderer-accessibility). When the tree is sparse the
environment falls back to the raw screenshot.
"""
import json
import sys

try:
    import pyatspi
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": f"pyatspi unavailable: {exc}", "elements": []}))
    sys.exit(0)

MAX_ELEMENTS = 200
SKIP_ROLES = {"filler", "panel", "separator", "scroll bar", "scroll pane"}


def walk(node, out):
    if len(out) >= MAX_ELEMENTS:
        return
    try:
        role = node.getRoleName()
        state = node.getState()
        if state.contains(pyatspi.STATE_SHOWING) and state.contains(pyatspi.STATE_VISIBLE):
            ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            if 0 < ext.width < 8000 and 0 < ext.height < 8000 and role not in SKIP_ROLES:
                name = (node.name or "").strip()
                if name or state.contains(pyatspi.STATE_FOCUSABLE):
                    out.append({"role": role, "name": name[:80],
                                "x": int(ext.x), "y": int(ext.y),
                                "w": int(ext.width), "h": int(ext.height)})
    except Exception:
        pass
    try:
        for i in range(node.childCount):
            walk(node.getChildAtIndex(i), out)
    except Exception:
        pass


def main():
    out = []
    try:
        desktop = pyatspi.Registry.getDesktop(0)
        for i in range(desktop.childCount):
            walk(desktop.getChildAtIndex(i), out)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc), "elements": []}))
        return
    print(json.dumps({"elements": out}))


if __name__ == "__main__":
    main()
