---
name: my_environment
description: Human-readable description of the environment.
version: 1.0.0
type: worker
---

<environment_my_environment>

## State
Describe what `get_state` returns for this environment (current status, what the agent observes each round).

## Vision
If the environment returns screenshots/images (has_vision=True), describe them here and how the agent should use them. Otherwise write "No vision available."

## Actions

### set_value
Store a value under a key.
- key (str): the key.
- value (str): the value to store.

### get_value
Read the value stored under a key.
- key (str): the key.

## Interaction
Input format: a JSON string with action-specific parameters.
Example: {"name": "set_value", "args": {"key": "foo", "value": "bar"}}

</environment_my_environment>
