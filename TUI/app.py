from __future__ import annotations

import argparse
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
)

from TUI.data_source import HarnessDataSource, RunSummary


RUNTIME_REPAIR_COPY = (
    "Re-validating statically every attempt · repair prompts scoped to runtime failure"
)


def _status_text(checkpoint: dict[str, Any]) -> str:
    session = checkpoint.get("session") or checkpoint
    return str(session.get("final_status") or checkpoint.get("phase") or "running")


def _attempt_lines(checkpoint: dict[str, Any]) -> list[str]:
    session = checkpoint.get("session") or checkpoint
    lines: list[str] = []
    for attempt in session.get("attempts", []):
        validation = attempt.get("validation") or {}
        behavior = attempt.get("behavior_validation") or {}
        formal = attempt.get("formal_validation") or {}
        static_count = len(validation.get("violations") or [])
        behavior_issues = behavior.get("issues") or []
        formal_issues = formal.get("issues") or []
        worker = attempt.get("draft_source_worker") or attempt.get("repair_worker") or "unknown"
        behavior_label = (
            "compliant"
            if behavior.get("is_compliant", True)
            else f"mismatch on {behavior_issues[0].get('case', 'case')}"
        )
        formal_label = (
            "skipped"
            if formal.get("skipped")
            else "compliant"
            if formal.get("is_compliant", True)
            else f"{len(formal_issues)} issues"
        )
        lines.extend(
            [
                f"[attempt {attempt.get('attempt', '?')}] draft_source_worker={worker}",
                (
                    "  static:   compliant"
                    if validation.get("is_compliant", True)
                    else f"  static:   {static_count} violations"
                ),
                f"  behavior: {behavior_label}",
                f"  formal:   {formal_label}",
                "",
            ]
        )
        retry_prompt = str(attempt.get("retry_prompt") or "").lower()
        if (
            not behavior.get("is_compliant", True)
            and validation.get("is_compliant", True)
            and retry_prompt
        ):
            lines.append(RUNTIME_REPAIR_COPY)
            lines.append("")
    return lines


class ArchitectureModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, data_source: HarnessDataSource, root: Path | str) -> None:
        super().__init__()
        self.data_source = data_source
        self.root = str(root)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Label("Repository architecture", classes="title")
            yield Input(value=self.root, id="architecture-root")
            with Horizontal(classes="actions"):
                yield Button("Refresh", id="refresh-map", variant="primary")
                yield Button(
                    "Open SVG",
                    id="open-svg",
                    disabled=not self.data_source.mermaid_renderer_available(),
                )
                yield Button("Close", id="close-modal")
            yield RichLog(id="architecture-output", wrap=False, highlight=True)

    def on_mount(self) -> None:
        self.refresh_map()

    def refresh_map(self) -> None:
        output = self.query_one("#architecture-output", RichLog)
        output.clear()
        root = self.query_one("#architecture-root", Input).value
        try:
            output.write(self.data_source.repo_map(root, "mermaid"))
        except Exception as exc:  # noqa: BLE001 - shown in the review UI
            output.write(f"Repo map failed: {type(exc).__name__}: {exc}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-modal":
            self.dismiss()
        elif event.button.id == "refresh-map":
            self.refresh_map()
        elif event.button.id == "open-svg":
            root = self.query_one("#architecture-root", Input).value
            try:
                path = self.data_source.open_mermaid_svg(root)
                self.notify(f"Opened {path}")
            except Exception as exc:  # noqa: BLE001 - actionable UI notification
                self.notify(str(exc), severity="error")


class ChangesModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, data_source: HarnessDataSource, run_id: str) -> None:
        super().__init__()
        self.data_source = data_source
        self.run_id = run_id
        self.units: list[tuple[str, str | None]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Label(f"Changes · {self.run_id}", classes="title")
            with Horizontal(classes="actions"):
                yield Button("Load diff", id="load-diff", variant="primary")
                yield Button("Close", id="close-modal")
            with Horizontal(id="changes-panes"):
                yield DataTable(id="changes-tree", cursor_type="row")
                yield RichLog(id="diff-output", wrap=False, highlight=True)

    def on_mount(self) -> None:
        tree = self.query_one("#changes-tree", DataTable)
        tree.add_column("Source / contract")
        self.units = self.data_source.list_change_units(self.run_id)
        for label, contract_name in self.units:
            tree.add_row(label, key=contract_name or label)
        self.load_diff()

    def load_diff(self) -> None:
        output = self.query_one("#diff-output", RichLog)
        output.clear()
        tree = self.query_one("#changes-tree", DataTable)
        contract_name = (
            self.units[tree.cursor_row][1]
            if self.units and tree.cursor_row < len(self.units)
            else None
        )
        hunks = self.data_source.diff_attempts(self.run_id, contract_name)
        if not hunks:
            output.write("No successive attempt sources are available for this selection.")
            return
        for index, hunk in enumerate(hunks, start=1):
            output.write(f"--- change {index} ---")
            output.write(hunk.diff)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-modal":
            self.dismiss()
        elif event.button.id == "load-diff":
            self.load_diff()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "changes-tree":
            self.load_diff()


class HistoryScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, data_source: HarnessDataSource, signature: str = "") -> None:
        super().__init__()
        self.data_source = data_source
        self.signature = signature

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="screen-body"):
            yield Label("Similar past attempts", classes="title")
            yield Input(
                value=self.signature,
                placeholder="Describe a task to search local run history",
                id="history-signature",
            )
            yield Button("Search", id="search-history", variant="primary")
            yield RichLog(id="history-output", wrap=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        if self.signature:
            self.search()

    def search(self) -> None:
        signature = self.query_one("#history-signature", Input).value
        output = self.query_one("#history-output", RichLog)
        output.clear()
        matches = self.data_source.similar_past_attempts(signature)
        if not matches:
            output.write("No sufficiently similar prior attempts found.")
            return
        output.write("Advisory only · current runtime evidence and human review remain authoritative.\n")
        for match in matches:
            output.write(
                f"{match.get('score', 0):.2f} · {match.get('source', 'history')} · "
                f"{match.get('final_status', 'unknown')}"
            )
            output.write(str(match.get("signature") or "(no signature)"))
            failures = ", ".join(match.get("failure_kinds") or [])
            if failures:
                output.write(f"failures: {failures}")
            if match.get("lesson"):
                output.write(f"lesson: {match['lesson']}")
            output.write("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search-history":
            self.search()


class LiveRunScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("m", "architecture", "Architecture"),
        ("d", "changes", "Changes"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(
        self,
        data_source: HarnessDataSource,
        *,
        process: subprocess.Popen[str] | None = None,
        run_id: str = "",
        initial_run_ids: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.data_source = data_source
        self.process = process
        self.run_id = run_id
        self.initial_run_ids = initial_run_ids or set()
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._rendered_attempts = -1

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Waiting for checkpoint…", id="live-status")
        with Horizontal(id="live-panes"):
            with Vertical(classes="pane"):
                yield Label("Event log", classes="title")
                yield RichLog(id="event-log", wrap=False, highlight=True)
            with Vertical(classes="pane"):
                yield Label("Contract queue", classes="title")
                yield DataTable(id="contract-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#contract-table", DataTable)
        table.add_columns("State", "Contract", "Status / wait")
        self.set_interval(0.75, self.refresh_run)
        if self.process is not None and self.process.stdout is not None:
            threading.Thread(target=self._read_process_output, daemon=True).start()
        self.refresh_run()

    def _read_process_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self._output_queue.put(line.rstrip())

    def refresh_run(self) -> None:
        log = self.query_one("#event-log", RichLog)
        while True:
            try:
                log.write(self._output_queue.get_nowait())
            except queue.Empty:
                break
        if not self.run_id:
            for run in self.data_source.list_runs():
                if run.run_id not in self.initial_run_ids:
                    self.run_id = run.run_id
                    break
        if not self.run_id:
            return
        try:
            checkpoint = self.data_source.load_checkpoint(self.run_id)
        except Exception as exc:  # noqa: BLE001 - transient file update
            self.query_one("#live-status", Static).update(f"{self.run_id} · {exc}")
            return
        if not checkpoint:
            return
        self.query_one("#live-status", Static).update(
            f"{self.run_id} · {_status_text(checkpoint)}"
        )
        session = checkpoint.get("session") or checkpoint
        attempt_count = len(session.get("attempts") or [])
        if attempt_count != self._rendered_attempts:
            log.clear()
            for line in _attempt_lines(checkpoint):
                log.write(line)
            self._rendered_attempts = attempt_count
        self._render_contracts(checkpoint)

    def _render_contracts(self, checkpoint: dict[str, Any]) -> None:
        table = self.query_one("#contract-table", DataTable)
        table.clear()
        for result in checkpoint.get("contract_results", []):
            status = str(result.get("status") or "unknown")
            icon = {
                "accepted": "✓",
                "validation_failed": "✕",
                "dependency_blocked": "…",
            }.get(status, "·")
            if status == "dependency_blocked":
                details = f"waiting on: {', '.join(result.get('dependencies') or [])}"
            elif status == "validation_failed":
                details = f"{len(result.get('issues') or [])} issues"
            else:
                details = status
            table.add_row(icon, str(result.get("name") or ""), details)

    def action_architecture(self) -> None:
        root = (
            self.data_source.artifact_root / self.run_id
            if self.run_id
            else self.data_source.repo_root
        )
        self.app.push_screen(ArchitectureModal(self.data_source, root))

    def action_changes(self) -> None:
        if self.run_id:
            self.app.push_screen(ChangesModal(self.data_source, self.run_id))
        else:
            self.notify("No run checkpoint selected yet", severity="warning")


class RunLauncherScreen(Screen):
    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("r", "resume", "Resume"),
        ("m", "architecture", "Architecture"),
        ("d", "changes", "Changes"),
        ("h", "history", "History"),
    ]

    def __init__(self, data_source: HarnessDataSource) -> None:
        super().__init__()
        self.data_source = data_source
        self._runs: list[RunSummary] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="screen-body"):
            yield Label("Launch or review a harness run", classes="title")
            with Horizontal(classes="form-row"):
                yield Select(
                    [(name.replace("_", " ").title(), name) for name in self.data_source.available_entrypoints()],
                    value="coding_capability",
                    id="entrypoint",
                    allow_blank=False,
                )
                yield Select(
                    [("Ollama (local)", "ollama"), ("DeepSeek (architect)", "deepseek_architect")],
                    value="ollama",
                    id="provider",
                    allow_blank=False,
                )
            yield Input(
                placeholder="Structured spec path, or optional task file",
                id="source-path",
            )
            yield Input(
                placeholder="Task description for similar-history hints",
                id="task-signature",
            )
            yield Static("", id="history-hints")
            with Horizontal(classes="actions"):
                yield Button("Launch", id="launch-run", variant="success")
                yield Button("Resume", id="resume-run", variant="primary")
                yield Button("Refresh runs", id="refresh-runs")
            yield Label("Checkpointed runs", classes="title")
            yield DataTable(id="runs-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#runs-table", DataTable)
        table.add_columns("Run ID", "Kind", "Status", "Attempts", "Updated")
        self.refresh_runs()

    def refresh_runs(self) -> None:
        self._runs = self.data_source.list_runs()
        table = self.query_one("#runs-table", DataTable)
        table.clear()
        for run in self._runs:
            table.add_row(
                run.run_id,
                run.kind,
                run.status,
                str(run.attempt_count),
                run.updated_at[:19],
                key=run.run_id,
            )

    def selected_run(self) -> RunSummary | None:
        table = self.query_one("#runs-table", DataTable)
        if table.row_count == 0 or table.cursor_row >= len(self._runs):
            return None
        return self._runs[table.cursor_row]

    def _launch_args(self) -> tuple[str, dict[str, str]]:
        entrypoint = str(self.query_one("#entrypoint", Select).value)
        provider = str(self.query_one("#provider", Select).value)
        source = self.query_one("#source-path", Input).value.strip()
        args = {"provider": provider}
        if source:
            args["spec" if entrypoint == "structured_spec" else "tasks"] = source
        return entrypoint, args

    def launch(self) -> None:
        entrypoint, args = self._launch_args()
        initial_ids = {run.run_id for run in self._runs}
        try:
            process = self.data_source.launch_run(entrypoint, args)
        except Exception as exc:  # noqa: BLE001 - user-facing validation
            self.notify(str(exc), severity="error")
            return
        self.app.push_screen(
            LiveRunScreen(
                self.data_source,
                process=process,
                initial_run_ids=initial_ids,
            )
        )

    def action_resume(self) -> None:
        run = self.selected_run()
        if run is None:
            self.notify("Select a checkpointed run first", severity="warning")
            return
        _selected_entrypoint, args = self._launch_args()
        entrypoint = run.entrypoint
        if entrypoint == "structured_spec":
            args.pop("tasks", None)
        try:
            process = self.data_source.resume_run(entrypoint, run.run_id, args)
        except Exception as exc:  # noqa: BLE001 - user-facing validation
            self.notify(str(exc), severity="error")
            return
        self.app.push_screen(
            LiveRunScreen(
                self.data_source,
                process=process,
                run_id=run.run_id,
            )
        )

    def action_architecture(self) -> None:
        self.app.push_screen(
            ArchitectureModal(self.data_source, self.data_source.repo_root)
        )

    def action_changes(self) -> None:
        run = self.selected_run()
        if run is None:
            self.notify("Select a checkpointed run first", severity="warning")
            return
        self.app.push_screen(ChangesModal(self.data_source, run.run_id))

    def action_history(self) -> None:
        signature = self.query_one("#task-signature", Input).value
        self.app.push_screen(HistoryScreen(self.data_source, signature))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "task-signature":
            return
        matches = self.data_source.similar_past_attempts(event.value)
        hints = self.query_one("#history-hints", Static)
        if not matches:
            hints.update("")
            return
        hints.update(
            "Seen something like this before (advisory): "
            + " · ".join(
                f"{match.get('signature', '')} [{match.get('final_status', 'unknown')}]"
                for match in matches
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "launch-run":
            self.launch()
        elif event.button.id == "resume-run":
            self.action_resume()
        elif event.button.id == "refresh-runs":
            self.refresh_runs()


class HarnessTUI(App[None]):
    TITLE = "Agent Small Harness"
    SUB_TITLE = "Human review console"
    CSS = """
    Screen {
        background: $background;
    }
    #screen-body {
        padding: 1 2;
    }
    .title {
        text-style: bold;
        margin: 1 0;
    }
    .form-row, .actions {
        height: auto;
        margin: 1 0;
    }
    .form-row > Select {
        width: 1fr;
        margin-right: 1;
    }
    .actions > Button {
        margin-right: 1;
    }
    #history-hints {
        color: $warning;
        margin: 1 0;
    }
    #runs-table {
        height: 1fr;
        min-height: 12;
    }
    #live-status {
        height: 3;
        padding: 1 2;
        background: $surface;
    }
    #live-panes {
        height: 1fr;
    }
    .pane {
        width: 1fr;
        padding: 0 1;
        border: solid $primary-background;
    }
    #event-log, #contract-table, #history-output {
        height: 1fr;
    }
    ArchitectureModal, ChangesModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }
    #modal {
        width: 90%;
        height: 90%;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #architecture-output, #diff-output {
        height: 1fr;
    }
    #changes-panes {
        height: 1fr;
    }
    #changes-tree {
        width: 30%;
        height: 1fr;
        margin-right: 1;
    }
    #diff-output {
        width: 70%;
    }
    """

    def __init__(self, data_source: HarnessDataSource | None = None) -> None:
        super().__init__()
        self.data_source = data_source or HarnessDataSource()

    def on_mount(self) -> None:
        self.push_screen(RunLauncherScreen(self.data_source))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the Agent Small Harness TUI.")
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--history", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    kwargs: dict[str, Any] = {}
    if args.artifact_root is not None:
        kwargs["artifact_root"] = args.artifact_root
    if args.history is not None:
        kwargs["history_path"] = args.history
    if args.repo_root is not None:
        kwargs["repo_root"] = args.repo_root
    HarnessTUI(HarnessDataSource(**kwargs)).run()
    return 0
