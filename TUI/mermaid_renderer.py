"""Small, bounded Mermaid-flowchart renderer for terminal review.

The repository mapper remains the source of truth. This module only turns its
simple node/edge flowchart output into a readable terminal tree; it is not a
general Mermaid implementation.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.repo_map_agent import RepoGraph


_NODE_PATTERN = re.compile(r'^\s*(\w+)\["(.*)"\]\s*$')
_EDGE_PATTERN = re.compile(r"^\s*(\w+)\s+-->\|([^|]*)\|\s+(\w+)\s*$")


@dataclass(frozen=True)
class MermaidEdge:
    source: str
    target: str
    label: str


def render_repo_architecture_mermaid(
    graph: RepoGraph,
    *,
    focus: str = "",
) -> str:
    """Render a small layer-level Mermaid diagram suitable for a browser."""

    records = [record for record in graph.files if not record.parse_error]
    grouped, module_dependencies = _architecture_data(graph, records)
    focus_key = focus.strip().lower()
    selected_layers = {
        layer
        for layer, layer_records in grouped.items()
        if not focus_key
        or focus_key in layer.lower()
        or any(
            focus_key in record.module.lower()
            or focus_key in record.path.lower()
            for record in layer_records
        )
    }
    if focus_key:
        neighbors = {
            dependency
            for module, dependencies in module_dependencies.items()
            if module.split(".", 1)[0] in selected_layers
            for dependency in dependencies
        }
        selected_layers.update(neighbors)
    selected_layers &= set(grouped)
    if not selected_layers:
        return "flowchart LR\n  empty[\"No matching architecture layers\"]"

    layer_ids = {
        layer: "layer_" + re.sub(r"\W+", "_", layer).strip("_")
        for layer in sorted(selected_layers)
    }
    lines = ["flowchart LR"]
    for layer in sorted(selected_layers):
        layer_records = grouped[layer]
        functions = sum(len(record.functions) for record in layer_records)
        lines.append(
            f'  {layer_ids[layer]}["{layer}/<br/>{len(layer_records)} modules'
            f'<br/>{functions} functions"]'
        )

    layer_edges: Counter[tuple[str, str]] = Counter()
    for module, dependencies in module_dependencies.items():
        source_layer = module.split(".", 1)[0]
        if source_layer not in selected_layers:
            continue
        for target_layer, count in dependencies.items():
            if target_layer in selected_layers:
                layer_edges[(source_layer, target_layer)] += count
    for (source_layer, target_layer), count in sorted(layer_edges.items()):
        lines.append(
            f"  {layer_ids[source_layer]} -->|{count} imports| "
            f"{layer_ids[target_layer]}"
        )
    return "\n".join(lines)


def render_repo_architecture(
    graph: RepoGraph,
    *,
    focus: str = "",
    max_layers: int = 14,
    max_modules: int = 10,
) -> str:
    """Render a repo map as human-scale layers instead of AST node noise."""

    records = [record for record in graph.files if not record.parse_error]
    if not records:
        return "No parseable Python files were found."

    focus_key = focus.strip().lower()
    grouped, module_dependencies = _architecture_data(graph, records)

    selected: list[tuple[str, list]] = []
    for layer, layer_records in sorted(grouped.items()):
        if focus_key and focus_key not in layer.lower():
            matching = [
                record
                for record in layer_records
                if focus_key in record.module.lower()
                or focus_key in record.path.lower()
            ]
            if not matching:
                continue
            layer_records = matching
        selected.append((layer, sorted(layer_records, key=lambda item: item.module)))

    if not selected:
        return f"No architecture layers or modules match {focus!r}."

    lines = [
        f"Repository architecture · {graph.root}",
        (
            f"{len(records)} Python files · {len(grouped)} top-level layers · "
            f"{sum(len(record.functions) for record in records)} functions"
        ),
        "Layer view groups AST data for human review. Select Raw node tree for low-level details.",
        "",
    ]
    for layer, layer_records in selected[:max_layers]:
        functions = sum(len(record.functions) for record in layer_records)
        variables = sum(len(record.variables) for record in layer_records)
        loops = sum(len(record.loops) for record in layer_records)
        lines.append(
            f"{layer}/  {len(layer_records)} modules · {functions} functions · "
            f"{variables} variables · {loops} loops"
        )
        module_names = [
            record.module.split(".", 1)[1]
            if "." in record.module
            else record.module
            for record in layer_records
        ]
        shown_modules = module_names[:max_modules]
        module_text = ", ".join(shown_modules)
        if len(module_names) > max_modules:
            module_text += f", +{len(module_names) - max_modules} more"
        lines.append(f"  ├─ modules: {module_text or '(package root)'}")
        layer_dependencies: Counter[str] = Counter()
        for record in layer_records:
            layer_dependencies.update(module_dependencies.get(record.module, Counter()))
        if layer_dependencies:
            dependency_text = ", ".join(
                f"{name} ({count})"
                for name, count in layer_dependencies.most_common(8)
            )
            lines.append(f"  └─ depends on: {dependency_text}")
        else:
            lines.append("  └─ depends on: none outside this layer")
        lines.append("")

    omitted = len(selected) - max_layers
    if omitted > 0:
        lines.append(f"… {omitted} additional layers omitted")
    if focus_key:
        lines.append(f"Filter active: {focus}")
    return "\n".join(lines).rstrip()


def _architecture_data(
    graph: RepoGraph,
    records: list,
) -> tuple[dict[str, list], dict[str, Counter[str]]]:
    grouped: dict[str, list] = defaultdict(list)
    for record in records:
        layer = record.module.split(".", 1)[0] if record.module else "(root)"
        grouped[layer].append(record)

    node_modules = {node.id: node.module for node in graph.nodes}
    node_kinds = {node.id: node.kind for node in graph.nodes}
    module_dependencies: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in graph.edges:
        if edge.kind != "imports":
            continue
        source_module = node_modules.get(edge.source, "")
        target_module = node_modules.get(edge.target, "")
        if not source_module or not target_module:
            continue
        source_layer = source_module.split(".", 1)[0]
        target_layer = target_module.split(".", 1)[0]
        if (
            source_layer != target_layer
            and target_layer in grouped
            and node_kinds.get(edge.target) in {"module", "local_module"}
        ):
            module_dependencies[source_module][target_layer] += 1
    return grouped, module_dependencies


def render_mermaid_tree(
    source: str,
    *,
    max_roots: int = 10,
    max_children: int = 8,
    max_depth: int = 1,
) -> str:
    """Render the repo mapper's Mermaid subset as a bounded indented tree."""

    nodes: dict[str, str] = {}
    edges: list[MermaidEdge] = []
    for line in source.splitlines():
        node_match = _NODE_PATTERN.match(line)
        if node_match:
            nodes[node_match.group(1)] = node_match.group(2)
            continue
        edge_match = _EDGE_PATTERN.match(line)
        if edge_match:
            edges.append(
                MermaidEdge(
                    source=edge_match.group(1),
                    label=edge_match.group(2),
                    target=edge_match.group(3),
                )
            )

    if not nodes:
        return "No repository-map nodes were produced."

    outgoing: dict[str, list[MermaidEdge]] = defaultdict(list)
    incoming: set[str] = set()
    for edge in edges:
        if edge.source not in nodes or edge.target not in nodes:
            continue
        outgoing[edge.source].append(edge)
        incoming.add(edge.target)
    for node_edges in outgoing.values():
        node_edges.sort(key=lambda edge: (edge.label, nodes[edge.target]))

    module_roots = sorted(
        (
            node_id
            for node_id, label in nodes.items()
            if label.startswith("module: ")
        ),
        key=lambda node_id: nodes[node_id],
    )
    roots = module_roots or sorted(
        (node_id for node_id in nodes if node_id not in incoming),
        key=lambda node_id: nodes[node_id],
    )
    if not roots:
        roots = sorted(nodes, key=nodes.get)

    lines = [
        f"Repository map · {len(nodes)} nodes · {len(edges)} edges",
        (
            f"Compact view: first {min(len(roots), max_roots)} roots, "
            f"depth {max_depth}, {max_children} children per node"
        ),
        "Use “Full Mermaid source” only when raw graph text is needed.",
        "",
    ]
    for root_index, root in enumerate(roots[:max_roots]):
        if root_index:
            lines.append("")
        lines.append(nodes[root])
        _render_children(
            root,
            nodes=nodes,
            outgoing=outgoing,
            lines=lines,
            prefix="",
            depth=0,
            max_depth=max_depth,
            max_children=max_children,
            path={root},
        )
    omitted_roots = len(roots) - max_roots
    if omitted_roots > 0:
        lines.extend(["", f"… {omitted_roots} additional roots omitted"])
    return "\n".join(lines)


def _render_children(
    node_id: str,
    *,
    nodes: dict[str, str],
    outgoing: dict[str, list[MermaidEdge]],
    lines: list[str],
    prefix: str,
    depth: int,
    max_depth: int,
    max_children: int,
    path: set[str],
) -> None:
    children = outgoing.get(node_id, [])
    shown = children[:max_children]
    for index, edge in enumerate(shown):
        last = index == len(shown) - 1 and len(children) <= max_children
        branch = "└─" if last else "├─"
        target_label = nodes[edge.target]
        cycle = edge.target in path
        lines.append(
            f"{prefix}{branch} {edge.label} → {target_label}"
            + (" ↩" if cycle else "")
        )
        if cycle or depth + 1 >= max_depth:
            continue
        _render_children(
            edge.target,
            nodes=nodes,
            outgoing=outgoing,
            lines=lines,
            prefix=prefix + ("   " if last else "│  "),
            depth=depth + 1,
            max_depth=max_depth,
            max_children=max_children,
            path={*path, edge.target},
        )
    omitted = len(children) - len(shown)
    if omitted > 0:
        lines.append(f"{prefix}└─ … {omitted} more direct neighbors")
