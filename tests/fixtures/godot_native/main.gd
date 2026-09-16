extends Node3D

var player: CharacterBody3D
var label: Label
var clicks := 0
var material: StandardMaterial3D

func _ready() -> void:
	var camera := Camera3D.new()
	add_child(camera)
	camera.position = Vector3(0, 6, 10)
	camera.look_at(Vector3.ZERO)
	camera.current = true
	var light := DirectionalLight3D.new()
	add_child(light)
	light.rotation_degrees = Vector3(-55, -20, 0)
	var ground := MeshInstance3D.new()
	ground.mesh = BoxMesh.new()
	ground.scale = Vector3(12, 0.2, 8)
	ground.position.y = -0.6
	add_child(ground)
	player = CharacterBody3D.new()
	player.name = "Player"
	add_child(player)
	var mesh := MeshInstance3D.new()
	mesh.mesh = CapsuleMesh.new()
	material = StandardMaterial3D.new()
	material.albedo_color = Color(1, 0.35, 0.05)
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mesh.material_override = material
	player.add_child(mesh)
	var shape := CollisionShape3D.new()
	shape.shape = CapsuleShape3D.new()
	player.add_child(shape)
	var canvas := CanvasLayer.new()
	add_child(canvas)
	label = Label.new()
	label.position = Vector2(25, 25)
	label.add_theme_font_size_override("font_size", 24)
	canvas.add_child(label)
	var button := Button.new()
	button.name = "ColorButton"
	button.text = "Change companion color"
	button.position = Vector2(25, 85)
	button.size = Vector2(300, 60)
	button.pressed.connect(func():
		clicks += 1
		material.albedo_color = Color(0.1, 0.9, 0.35)
		print("BUTTON_CLICKED"))
	canvas.add_child(button)
	print("FIXTURE_READY")

func _physics_process(_delta: float) -> void:
	var held := Input.is_key_pressed(KEY_RIGHT)
	player.velocity = Vector3(3.0 if held else 0.0, 0, 0)
	player.move_and_slide()
	label.text = "Native 3D | X: %.2f | Clicks: %d" % [player.position.x, clicks]
	var evidence_path := "res://observed.json" if OS.has_feature("editor") else "user://observed.json"
	var evidence := FileAccess.open(evidence_path, FileAccess.WRITE)
	evidence.store_string(JSON.stringify({"x": player.position.x, "held": held, "clicks": clicks}))
