file_system_memory = dict(
    type = "FileSystemMemory",
    name = "file_system_memory",
    base_dir = f"work_dir/memory/file_system",
    model_name = "openrouter/gemini-3-flash-preview",
    max_todo_length = 80,
    require_grad = False,
)
