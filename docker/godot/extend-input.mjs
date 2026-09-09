// A narrow, checked extension to the pinned upstream build: mouse down/up and
// double-click events. Keep the original auto-releasing click behavior by default.
import { readFileSync, writeFileSync } from 'node:fs';

function replaceOnce(path, before, after) {
  const content = readFileSync(path, 'utf8');
  if (content.split(before).length !== 2) throw new Error(`Pinned input source changed: ${path}`);
  writeFileSync(path, content.replace(before, after));
}
for (const prefix of ['src', 'build']) {
  const root = `/opt/godot-mcp/${prefix}`;
  const ext = prefix === 'src' ? 'ts' : 'js';
  const definitions = `${root}/tool-definitions.${ext}`;
  const content = readFileSync(definitions, 'utf8');
  const start = content.indexOf("name: 'game_click'");
  const end = content.indexOf("name: 'game_key_press'", start);
  if (start < 0 || end < 0) throw new Error('Pinned click schema missing');
  const section = content.slice(start, end);
  const needle = 'properties: {';
  if (section.split(needle).length !== 2) throw new Error('Ambiguous click schema');
  const patched = section.replace(needle, `${needle}
      pressed: { type: 'boolean', description: 'Explicit mouse button down/up; omit for auto-releasing click' },
      doubleClick: { type: 'boolean', description: 'Set the native double-click event flag' },`);
  writeFileSync(definitions, content.slice(0, start) + patched + content.slice(end));
  replaceOnce(`${root}/tool-handlers/game-tool-handlers.${ext}`,
    'button: a.button ?? 1 }',
    'button: a.button ?? 1, pressed: a.pressed, double_click: a.doubleClick }');

  const domain = `${root}/scripts/mcp_runtime/input_domain.gd`;
  replaceOnce(domain, 'var _active_touches: Dictionary = {}',
    'var _active_touches: Dictionary = {}\nvar _held_mouse_buttons: Dictionary = {}');
  replaceOnce(domain, '\t_release_all_touches()\n', `\t_release_all_touches()
\tfor button in _held_mouse_buttons:
\t\tvar event := InputEventMouseButton.new()
\t\tevent.button_index = button as MouseButton
\t\tevent.position = _held_mouse_buttons[button]
\t\tevent.pressed = false
\t\tInput.parse_input_event(event)
\t_held_mouse_buttons.clear()
`);
  replaceOnce(domain, '\n\tvar pos: Vector2 = Vector2(x, y)\n', `
\tvar pressed: bool = reader.optional_bool("pressed", true)
\tvar double_click: bool = reader.optional_bool("double_click", false)
\tif params_invalid(reader):
\t\treturn
\tvar pos: Vector2 = Vector2(x, y)
\tif params.has("pressed"):
\t\tvar event := InputEventMouseButton.new()
\t\tevent.position = pos
\t\tevent.global_position = pos
\t\tevent.button_index = button as MouseButton
\t\tevent.pressed = pressed
\t\tevent.double_click = double_click
\t\tif pressed:
\t\t\t_held_mouse_buttons[button] = pos
\t\telse:
\t\t\t_held_mouse_buttons.erase(button)
\t\tvar mask := 0
\t\tfor held_button in _held_mouse_buttons:
\t\t\tmask |= _mouse_button_mask(held_button)
\t\tevent.button_mask = mask as MouseButtonMask
\t\tInput.parse_input_event(event)
\t\trespond({"success": true, "pressed": pressed, "button": button})
\t\treturn
`);
  replaceOnce(domain, '\n\tpress_event.button_index = button as MouseButton\n\tpress_event.pressed = true\n',
    '\n\tpress_event.button_index = button as MouseButton\n\tpress_event.pressed = true\n\tpress_event.double_click = double_click\n');
  replaceOnce(domain, '\t_parse_synthetic_mouse_motion(event)\n', `\tvar mask := 0
\tfor held_button in _held_mouse_buttons:
\t\tmask |= _mouse_button_mask(held_button)
\t\t_held_mouse_buttons[held_button] = event.position
\tevent.button_mask = mask as MouseButtonMask
\t_parse_synthetic_mouse_motion(event)
`);
  replaceOnce(domain, 'func _parse_synthetic_mouse_motion(event: InputEventMouseMotion) -> void:\n',
    `func _apply_current_mouse_modifiers(event: InputEventMouse) -> void:
\tevent.shift_pressed = Input.is_key_pressed(KEY_SHIFT)
\tevent.ctrl_pressed = Input.is_key_pressed(KEY_CTRL)
\tevent.alt_pressed = Input.is_key_pressed(KEY_ALT)
\tevent.meta_pressed = Input.is_key_pressed(KEY_META)
\tif event is InputEventMouseButton:
\t\tif event.pressed:
\t\t\t_held_mouse_buttons[event.button_index] = event.position
\t\telse:
\t\t\t_held_mouse_buttons.erase(event.button_index)
\tvar mask := 0
\tfor held_button in _held_mouse_buttons:
\t\tmask |= _mouse_button_mask(held_button)
\tevent.button_mask = mask as MouseButtonMask


func _parse_synthetic_mouse_motion(event: InputEventMouseMotion) -> void:
\t_apply_current_mouse_modifiers(event)
`);
  // Restrict the rewrite to mouse handlers; key_press also has a release_event.
  let mouseSource = readFileSync(domain, 'utf8');
  const mouseCalls = /^(\t+)Input\.parse_input_event\((press_event|release_event)\)$/gm;
  for (const handler of ['_cmd_click', '_cmd_scroll', '_cmd_mouse_drag']) {
    const begin = mouseSource.indexOf(`func ${handler}(`);
    const end = mouseSource.indexOf('\nfunc ', begin + 1);
    if (begin < 0 || end < 0) throw new Error('Pinned mouse handler missing');
    const section = mouseSource.slice(begin, end);
    if ([...section.matchAll(mouseCalls)].length !== 2) throw new Error(`Pinned mouse event sites changed: ${handler}`);
    mouseSource = mouseSource.slice(0, begin) + section.replace(mouseCalls,
      '$1_apply_current_mouse_modifiers($2)\n$1Input.parse_input_event($2)') + mouseSource.slice(end);
  }
  writeFileSync(domain, mouseSource);
  replaceOnce(domain, '\t\tevent.double_click = double_click\n',
    '\t\tevent.double_click = double_click\n\t\t_apply_current_mouse_modifiers(event)\n');
}
