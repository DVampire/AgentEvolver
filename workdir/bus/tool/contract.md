0001
Tool: bash_tool
Description: Execute bash commands in the shell. 

IMPORTANT:
- Use this tool to run system commands, scripts, or any bash operations. 
- Be careful with commands that modify the system or require elevated privileges. 
- For file operations, ALWAYS use ABSOLUTE paths to avoid path-related issues. 
- Input should be a VALID bash command string.

Args:
- command (str): The command to execute. If file path is necessary, it should be an absolute path.

Example: {"name": "bash_tool", "args": {"command": "ls -l /path/to/file.txt"}}.


---
0002
Tool: python_interpreter_tool
Description: Execute Python code and return the output.
Use this tool to run Python scripts, perform calculations, or execute any Python code.
The tool provides a safe execution environment with access to standard Python libraries.

Args:
- code (str): The Python code to execute.

Example: {"name": "python_interpreter_tool", "args": {"code": "print('Hello, World!')"}}.


---
0003
Tool: done_tool
Description: Done tool for indicating that the task has been completed.
Use this tool to signal that a task or subtask has been finished.
Provide the `result` and `reasoning` of the task in the result and reasoning parameters.

Args:
- result (str): The result of the task completion.
- reasoning (str): The analysis or explanation of the task completion.

Example: {"name": "done_tool", "args": {"reasoning": "The task has been completed successfully.","result": "The task has been completed."}}.


---
0004
Tool: todo_tool
Description: Todo tool for managing a todo.md file with task decomposition and step tracking.
When using this tool, only provide parameters that are relevant to the specific operation you are performing. Do not include unnecessary parameters.

Available `action` parameters:
1. add: Add a new step to the todo list at the end or after a specific step.
    - task: The description of the step.
    - priority: The priority of the step.
    - category: The category of the step.
    - parameters: Optional parameters for the step.
    - after_step_id: Optional step ID to insert after (if not provided, adds to end).
2. complete: Mark step as completed (success or failed).
    - step_id: The ID of the step to complete.
    - status: Completion status: "success" or "failed".
    - result: Result description (1-3 sentences).
3. update: Update step information.
    - step_id: The ID of the step to update.
    - task: New step description.
    - parameters: New step parameters.
4. list: List all steps with their status.
5. clear: Clear completed steps.
6. show: Show the complete todo.md file content.
7. export: Export todo.md to a specified path.
    - export_path: The target path to export the todo.md file.
8. cleanup: Clean up and remove the todo from cache (call when done with the todo list).

Example: {"name": "todo_tool", "args": {"action": "add", "task": "Task description", "priority": "high", "category": "work"}}
Example: {"name": "todo_tool", "args": {"action": "complete", "step_id": "step_1", "status": "success", "result": "Completed successfully"}}

The todo.md file is maintained in the base directory and follows a structured format for task management.


