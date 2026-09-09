"""Compile bounded player-input sequences; never expose arbitrary runtime mutation."""
import math
from copy import deepcopy
from typing import Annotated, Any

import jsonschema
from pydantic import WithJsonSchema

TOOLS = {
    "key_down": "game_key_hold", "key_up": "game_key_release", "key_tap": "game_key_press",
    "text": "game_key_press", "click": "game_click", "double_click": "game_click",
    "mouse_down": "game_click", "mouse_up": "game_click", "mouse_move": "game_mouse_move",
    "drag": "game_mouse_drag", "scroll": "game_scroll", "gamepad": "game_gamepad",
    "touch": "game_touch", "action_strength": "game_input_action",
    "mouse_mode": "game_input_state", "wait_frames": "game_wait",
}

# Describe each operation separately so a keyboard step cannot acquire unrelated
# mouse/gamepad fields. The connected MCP schema remains the runtime authority.
_ARGUMENT_PROPERTIES = {
    **{k: {"type": "string"} for k in (
        "key", "action", "text", "direction", "actionName", "mode", "frameType")},
    **{k: {"type": "boolean"} for k in ("ctrl", "shift", "alt", "meta", "physical")},
    **{k: {"type": "number"} for k in (
        "x", "y", "relative_x", "relative_y", "fromX", "fromY", "toX", "toY", "value")},
    "type": {"type": "string", "enum": ["button", "axis"]},
    "button": {"type": "integer", "enum": [1, 2, 3, 8, 9]},
    "index": {"type": "integer"}, "device": {"type": "integer"},
    "duration_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
    "steps": {"type": "integer", "minimum": 1, "maximum": 300},
    "frames": {"type": "integer", "minimum": 1, "maximum": 600},
    "amount": {"type": "integer", "minimum": 1, "maximum": 100},
    "strength": {"type": "number", "minimum": 0, "maximum": 1},
}

_STEP_FIELDS = {
    "key_down": "key action", "key_up": "key action",
    "key_tap": "key action ctrl shift alt meta physical", "text": "text",
    "click": "x y button", "double_click": "x y button",
    "mouse_down": "x y button", "mouse_up": "x y button",
    "mouse_move": "x y relative_x relative_y", "drag": "fromX fromY toX toY button steps",
    "scroll": "x y direction amount", "gamepad": "type index value device",
    "touch": "action x y index toX toY steps", "action_strength": "actionName strength",
    "mouse_mode": "mode", "wait": "duration_ms", "wait_frames": "frames frameType",
    "release_all": "",
}
_STEP_REQUIRED = {
    "text": "text", "click": "x y", "double_click": "x y",
    "mouse_down": "x y", "mouse_up": "x y", "mouse_move": "x y",
    "drag": "fromX fromY toX toY", "scroll": "x y direction",
    "gamepad": "type index value", "touch": "action x y",
    "action_strength": "actionName strength", "mouse_mode": "mode", "wait": "duration_ms",
}
INPUT_STEPS_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": 32,
    "description": "Ordered inputs. Each type accepts only its own arguments; omit unused optional fields.",
    "items": {"anyOf": [
        {
            "type": "object", "additionalProperties": False,
            "required": ["type"] if kind == "release_all" else ["type", "arguments"],
            "properties": {
                "type": {"type": "string", "enum": [kind]},
                "arguments": {
                    "type": "object", "additionalProperties": False,
                    "properties": {key: _ARGUMENT_PROPERTIES[key] for key in fields.split()},
                    "required": _STEP_REQUIRED.get(kind, "").split(),
                    **({"oneOf": [{"required": ["key"]}, {"required": ["action"]}]}
                       if kind in ("key_down", "key_up", "key_tap") else {}),
                },
            },
        }
        for kind, fields in _STEP_FIELDS.items()
    ]},
    "examples": [[{"type": "key_tap", "arguments": {"key": "Enter"}},
                  {"type": "wait", "arguments": {"duration_ms": 200}}]],
}
InputSteps = Annotated[list[dict[str, Any]], WithJsonSchema(INPUT_STEPS_SCHEMA)]


def compile_steps(steps, schemas):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ValueError("Provide 1..32 input steps")
    compiled, wait_ms = [], 0
    for index, step in enumerate(deepcopy(steps)):
        if not isinstance(step, dict) or set(step) - {"type", "arguments"}:
            raise ValueError(
                f'Step {index}: use only type and arguments; nest parameters as '
                '{"type":"key_tap","arguments":{"key":"Enter"}}'
            )
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
