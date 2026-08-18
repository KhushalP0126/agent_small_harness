# Agents

Model-facing orchestration belongs here. These modules decide what context a
model receives and route its proposed work through deterministic validation;
they do not directly trust or write model output.

- `plan_mode.py` turns a request into a structured task.
- `tool_calling_agent.py` lets the model inspect and act through the bounded tool registry.
- `generation_controller.py` coordinates generation, validation, and repair.
- `repo_map_agent.py` creates repository context for an agent or browser view.
- `execution_agent.py` runs isolated behavior checks and records traces.
- `library_discovery.py` and `library_doc_search.py` locate installed-library
  context and documentation for repair prompts.
- `historian.py` and `routing_policy.py` retain route outcomes and select from
  measured options.

Use [`../routing/`](../routing/) from a caller that needs to connect an agent
to the TUI or bridge. Validation engines live in [`../engines/`](../engines/).
For a file-by-file map, see [`../docs/FILE_INDEX.md`](../docs/FILE_INDEX.md).
