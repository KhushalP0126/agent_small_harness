import io
import json
from pathlib import Path

from harness_kernel.tui_bridge import Bridge, EventWriter, PROTOCOL_VERSION


def events(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_protocol_v7_graph_approval_and_action_flow(tmp_path: Path) -> None:
    stream = io.StringIO()
    bridge = Bridge(EventWriter(stream), tool_repository_root=tmp_path,
                    artifact_root=tmp_path / ".state" / "runs", memory_path=tmp_path / ".memory")
    bridge.handle({"cmd": "orchestrate", "goal": "build parser"})
    proposal = next(event for event in events(stream) if event["type"] == "graph_proposal")
    assert PROTOCOL_VERSION == 7
    assert [node["node_id"] for node in proposal["graph"]["nodes"]] == ["research", "implement", "validate"]
    assert not (tmp_path / "src").exists()
    bridge.handle({"cmd": "approve_graph", "session_id": proposal["session_id"],
                   "revision_hash": proposal["revision_hash"]})
    bridge.handle({"cmd": "orchestration_action", "session_id": proposal["session_id"], "action": "start"})
    states = [event["state"]["status"] for event in events(stream) if event["type"] == "orchestration_state"]
    assert states == ["approved", "running"]


def test_replay_emits_recorded_events_without_external_actions(tmp_path: Path) -> None:
    stream = io.StringIO()
    bridge = Bridge(EventWriter(stream), tool_repository_root=tmp_path,
                    artifact_root=tmp_path / ".state" / "runs", memory_path=tmp_path / ".memory")
    bridge.orchestrate("inspect only")
    proposal = next(event for event in events(stream) if event["type"] == "graph_proposal")
    bridge.replay_orchestration(proposal["session_id"])
    replay = [event for event in events(stream) if event["type"] == "orchestration_replay"][-1]
    assert replay["external_actions"] is False
    assert replay["events"][0]["event_type"] == "graph_proposed"
