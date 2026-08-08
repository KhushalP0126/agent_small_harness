# Agents

Model-facing work belongs here.

- `plan_mode.py` turns a request into a structured task.
- `tool_calling_agent.py` lets the model inspect and act through the bounded tool registry.
- `generation_controller.py` coordinates generation, validation, and repair.
- `repo_map_agent.py` creates repository context for an agent or browser view.
- `execution_agent.py` runs isolated behavior checks and records traces.

Use [`../routing/`](../routing/) from a caller that needs to connect an agent
to the TUI or bridge. Validation engines live in [`../engines/`](../engines/).
