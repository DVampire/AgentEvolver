extends "res://main.gd"

var entry: LineEdit
var slider: HSlider
var wheel_events := 0
var double_clicks := 0
var shift_clicks := 0
var drag_motion := 0
var relative_motion := Vector2.ZERO
var shortcut_count := 0
var joy_button_events := 0
var joy_axis_value := 0.0
var touches: Dictionary = {}
var touch_drags := 0

func _ready() -> void:
	super._ready()
	var canvas := CanvasLayer.new()
	add_child(canvas)
	entry = LineEdit.new()
	entry.name = "NameEntry"
	entry.position = Vector2(25, 165)
	entry.size = Vector2(300, 45)
	entry.placeholder_text = "Companion name"
	canvas.add_child(entry)
	slider = HSlider.new()
	slider.name = "VolumeSlider"
	slider.position = Vector2(25, 230)
	slider.size = Vector2(300, 35)
	slider.max_value = 100
	canvas.add_child(slider)
	var quit_button := Button.new()
	quit_button.text = "Quit game"
	quit_button.position = Vector2(25, 280)
	quit_button.size = Vector2(160, 40)
	quit_button.pressed.connect(func(): get_tree().create_timer(0.5).timeout.connect(get_tree().quit))
	canvas.add_child(quit_button)
	InputMap.add_action("test_boost")
	print("INTERACTION_READY")

func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed:
		if event.button_index in [MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]:
			wheel_events += 1
		if event.double_click:
			double_clicks += 1
		if event.shift_pressed and event.button_index == MOUSE_BUTTON_LEFT:
			shift_clicks += 1
	if event is InputEventMouseMotion:
		relative_motion += event.relative
		if event.button_mask != 0:
			drag_motion += 1
		if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
			get_viewport().get_camera_3d().rotation.y -= event.relative.x * 0.002
	if event is InputEventKey and event.pressed and event.ctrl_pressed and event.keycode == KEY_K:
		shortcut_count += 1
	if event is InputEventJoypadButton and event.pressed:
		joy_button_events += 1
	if event is InputEventJoypadMotion:
		joy_axis_value = event.axis_value
	if event is InputEventScreenTouch:
		if event.pressed:
			touches[event.index] = true
		else:
			touches.erase(event.index)
	if event is InputEventScreenDrag:
		touch_drags += 1

func _physics_process(delta: float) -> void:
	super._physics_process(delta)
	if Input.is_key_pressed(KEY_UP):
		player.position.z -= delta * 3
	if Input.is_key_pressed(KEY_RIGHT) and Input.is_key_pressed(KEY_SHIFT):
		player.position.x += delta * 3
	var state := {
		"x": player.position.x, "z": player.position.z,
		"right": Input.is_key_pressed(KEY_RIGHT), "shift": Input.is_key_pressed(KEY_SHIFT),
		"text": entry.text, "slider": slider.value,
		"wheel_events": wheel_events, "double_clicks": double_clicks, "shift_clicks": shift_clicks, "drag_motion": drag_motion,
		"relative_x": relative_motion.x, "shortcuts": shortcut_count,
		"left_mouse": Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT),
		"right_mouse": Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT),
		"camera_y": get_viewport().get_camera_3d().rotation.y,
		"joy_button_events": joy_button_events, "joy_axis_value": joy_axis_value,
		"joy_held": Input.is_joy_button_pressed(0, JOY_BUTTON_A),
		"joy_axis": Input.get_joy_axis(0, JOY_AXIS_LEFT_X),
		"touch_count": touches.size(), "touch_drags": touch_drags,
		"boost": Input.get_action_strength("test_boost"),
	}
	var path := "res://controls.json" if OS.has_feature("editor") else "user://controls.json"
	var file := FileAccess.open(path + ".tmp", FileAccess.WRITE)
	file.store_string(JSON.stringify(state))
	file.close()
	DirAccess.rename_absolute(path + ".tmp", path)
