"""Compile bounded player-input sequences; never expose arbitrary runtime mutation."""
import math
from copy import deepcopy

import jsonschema

TOOLS = {
    "key_down": "game_key_hold", "key_up": "game_key_release", "key_tap": "game_key_press",
    "text": "game_key_press", "click": "game_click", "double_click": "game_click",
    "mouse_down": "game_click", "mouse_up": "game_click", "mouse_move": "game_mouse_move",
    "drag": "game_mouse_drag", "scroll": "game_scroll", "gamepad": "game_gamepad",
    "touch": "game_touch", "action_strength": "game_input_action",
    "mouse_mode": "game_input_state", "wait_frames": "game_wait",
}


def compile_steps(steps, schemas):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ValueError("Provide 1..32 input steps")
    compiled, wait_ms = [], 0
    for index, step in enumerate(deepcopy(steps)):
        if not isinstance(step, dict) or set(step) - {"type", "arguments"}:
            raise ValueError(f"Step {index}: use only type and arguments")
        kind, args = step.get("type"), step.get("arguments", {})
        if not isinstance(args, dict):
            raise ValueError(f"Step {index}: arguments must be an object")
        # Reject NaN/Infinity before either JSON encoding or input side effects.
        if any(isinstance(v, float) and not math.isfinite(v) for v in args.values()):
            raise ValueError(f"Step {index}: coordinates/strength must be finite")
        if kind == "wait":
            duration = args.get("duration_ms")
            if set(args) != {"duration_ms"} or type(duration) is not int or not 0 <= duration <= 10000:
                raise ValueError("wait requires duration_ms in 0..10000")
            wait_ms += duration
            if wait_ms > 10000:
                raise ValueError("Combined waits must not exceed 10000 ms")
            compiled.append((kind, "", args))
            continue
        if kind == "release_all":
            if args:
                raise ValueError("release_all takes no arguments")
            compiled.append((kind, "", args))
            continue
        if kind not in TOOLS:
            raise ValueError(f"Unsupported input step: {kind}")
        tool = TOOLS[kind]
        if tool not in schemas:
            raise ValueError(f"MCP image does not support {tool}; rebuild docker/godot")
        if kind in ("click", "double_click", "mouse_down", "mouse_up"):
            if set(args) - {"x", "y", "button"}:
                raise ValueError("Mouse buttons accept x, y and button only")
            if args.get("button", 1) not in (1, 2, 3, 8, 9):
                raise ValueError("Use scroll for wheel input; button must be 1, 2, 3, 8 or 9")
            if kind in ("mouse_down", "mouse_up"):
                if "pressed" not in schemas[tool].get("properties", {}):
                    raise ValueError("Mouse hold requires the input2 Godot image")
                args["pressed"] = kind == "mouse_down"
            if kind == "double_click":
                args["doubleClick"] = True
        if kind == "text" and set(args) != {"text"}:
            raise ValueError("text accepts only the text field")
        if kind == "key_tap" and "pressed" in args:
            raise ValueError("Use key_down/key_up for persistent keyboard input")
        if kind == "mouse_mode":
            if set(args) != {"mode"}:
                raise ValueError("mouse_mode requires mode")
            args = {"action": "set_mouse_mode", "mouseMode": args["mode"]}
        if kind == "action_strength":
            if set(args) != {"actionName", "strength"} or not 0 <= args["strength"] <= 1:
                raise ValueError("action_strength requires actionName and strength in 0..1")
            args["action"] = "set_strength"
        if kind in ("drag", "touch") and not 1 <= args.get("steps", 10) <= 300:
            raise ValueError("Drag steps must be 1..300")
        if kind == "wait_frames" and not 1 <= args.get("frames", 1) <= 600:
            raise ValueError("Wait frames must be 1..600")
        if kind == "scroll" and not 1 <= args.get("amount", 1) <= 100:
            raise ValueError("Scroll amount must be 1..100")
        try:
            jsonschema.validate(args, schemas[tool])
        except jsonschema.ValidationError as error:
            raise ValueError(f"Step {index} ({kind}): {error.message}") from error
        compiled.append((kind, tool, args))
    return compiled


def remember_input(held, kind, args):
    """Track matching neutralizing commands only after an acknowledged input."""
    token, release, active = None, None, True
    if kind in ("key_down", "key_up"):
        token = "action:" + args["action"] if "action" in args else "key:" + args["key"].upper()
        active = kind == "key_down"
        release = ("game_key_release", args.copy())
    elif kind in ("mouse_down", "mouse_up"):
        token = f"mouse:{args.get('button', 1)}"
        active = kind == "mouse_down"
        release = ("game_click", {**args, "pressed": False})
    elif kind == "gamepad":
        token = f"gamepad:{args.get('device', 0)}:{args['type']}:{args['index']}"
        active = args["value"] != 0
        release = ("game_gamepad", {**args, "value": 0})
    elif kind == "touch":
        token = f"touch:{args.get('index', 0)}"
        active = args["action"] == "press"
        release = ("game_touch", {"action": "release", "x": args["x"], "y": args["y"], "index": args.get("index", 0)})
    elif kind == "action_strength":
        token = "action:" + args["actionName"]
        active = args["strength"] > 0
        release = ("game_input_action", {**args, "strength": 0})
    if token:
        if active:
            held[token] = release
        else:
            held.pop(token, None)
    if kind in ("click", "double_click", "drag"):
        held.pop(f"mouse:{args.get('button', 1)}", None)
    if kind == "key_tap":
        if "key" in args:
            held.pop("key:" + args["key"].upper(), None)
        if "action" in args:
            held.pop("action:" + args["action"], None)
    if kind == "mouse_move":
        for token, (tool, release_args) in held.items():
            if token.startswith("mouse:"):
                release_args.update(x=args["x"], y=args["y"])
