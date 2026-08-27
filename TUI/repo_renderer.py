"""Bounded, terminal-native repository graph renderers."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.repo_map_agent import RepoGraph


def render_repo_architecture(
    graph: RepoGraph,
    *,
    focus: str = "",
    max_layers: int = 14,
    max_modules: int = 10,
) -> str:
    records = [record for record in graph.files if not record.parse_error]
    if not records:
        return "No parseable Python files were found."
    grouped, dependencies = _architecture_data(graph, records)
    focus_key = focus.strip().lower()
    selected = []
    for layer, layer_records in sorted(grouped.items()):
        matches = [
            record
            for record in layer_records
            if not focus_key
            or focus_key in layer.lower()
            or focus_key in record.module.lower()
            or focus_key in record.path.lower()
        ]
        if matches:
            selected.append((layer, sorted(matches, key=lambda item: item.module)))
    if not selected:
        return f"No architecture layers or modules match {focus!r}."
    lines = [
        f"Repository architecture · {graph.root}",
        f"{len(records)} Python files · {len(grouped)} layers · "
        f"{sum(len(record.functions) for record in records)} functions",
        "",
    ]
    for layer, layer_records in selected[:max_layers]:
        lines.append(
            f"{layer}/  {len(layer_records)} modules · "
            f"{sum(len(record.functions) for record in layer_records)} functions · "
            f"{sum(len(record.variables) for record in layer_records)} variables"
        )
        names = [record.module.split(".", 1)[-1] for record in layer_records]
        shown = ", ".join(names[:max_modules])
        if len(names) > max_modules:
            shown += f", +{len(names) - max_modules} more"
        lines.append(f"  ├─ modules: {shown or '(package root)'}")
        layer_dependencies: Counter[str] = Counter()
        for record in layer_records:
            layer_dependencies.update(dependencies.get(record.module, Counter()))
        dependency_text = ", ".join(
            f"{name} ({count})" for name, count in layer_dependencies.most_common(8)
        )
        lines.append(f"  └─ depends on: {dependency_text or 'none outside this layer'}")
        lines.append("")
    if len(selected) > max_layers:
        lines.append(f"… {len(selected) - max_layers} additional layers omitted")
    return "\n".join(lines).rstrip()


def render_repo_tree(
    graph: RepoGraph,
    *,
    max_roots: int = 10,
    max_children: int = 8,
    max_depth: int = 2,
) -> str:
    nodes = {node.id: f"{node.kind}: {node.label}" for node in graph.nodes}
    outgoing: dict[str, list] = defaultdict(list)
    incoming: set[str] = set()
    for edge in graph.edges:
        if edge.source in nodes and edge.target in nodes:
            outgoing[edge.source].append(edge)
            incoming.add(edge.target)
    for edges in outgoing.values():
        edges.sort(key=lambda edge: (edge.kind, edge.label, nodes[edge.target]))
    roots = sorted(
        (node.id for node in graph.nodes if node.kind == "module"),
        key=lambda node_id: nodes[node_id],
    ) or sorted((node_id for node_id in nodes if node_id not in incoming), key=nodes.get)
    lines = [
        f"Repository graph · {len(nodes)} nodes · {len(graph.edges)} edges",
        f"Bounded tree · {min(len(roots), max_roots)} roots · depth {max_depth}",
        "",
    ]
    for index, root in enumerate(roots[:max_roots]):
        if index:
            lines.append("")
        lines.append(nodes[root])
        _render_children(
            root,
            nodes,
            outgoing,
            lines,
            prefix="",
            depth=0,
            max_depth=max_depth,
            max_children=max_children,
            path={root},
        )
    if len(roots) > max_roots:
        lines.extend(["", f"… {len(roots) - max_roots} additional roots omitted"])
    return "\n".join(lines)


def _render_children(
    node_id: str,
    nodes: dict[str, str],
    outgoing: dict[str, list],
    lines: list[str],
    *,
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
        label = edge.kind + (f": {edge.label}" if edge.label else "")
        cycle = edge.target in path
        lines.append(f"{prefix}{branch} {label} → {nodes[edge.target]}" + (" ↩" if cycle else ""))
        if not cycle and depth + 1 < max_depth:
            _render_children(
                edge.target,
                nodes,
                outgoing,
                lines,
                prefix=prefix + ("   " if last else "│  "),
                depth=depth + 1,
                max_depth=max_depth,
                max_children=max_children,
                path={*path, edge.target},
            )
    if len(children) > len(shown):
        lines.append(f"{prefix}└─ … {len(children) - len(shown)} more neighbors")


def _architecture_data(graph: RepoGraph, records: list) -> tuple[dict[str, list], dict[str, Counter[str]]]:
    grouped: dict[str, list] = defaultdict(list)
    for record in records:
        grouped[record.module.split(".", 1)[0] if record.module else "(root)"].append(record)
    node_modules = {node.id: node.module for node in graph.nodes}
    node_kinds = {node.id: node.kind for node in graph.nodes}
    dependencies: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in graph.edges:
        if edge.kind != "imports":
            continue
        source = node_modules.get(edge.source, "")
        target = node_modules.get(edge.target, "")
        target_layer = target.split(".", 1)[0]
        if (
            source
            and target
            and source.split(".", 1)[0] != target_layer
            and target_layer in grouped
            and node_kinds.get(edge.target) in {"module", "local_module"}
        ):
            dependencies[source][target_layer] += 1
    return grouped, dependencies
