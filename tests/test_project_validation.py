from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from harness_kernel.project_validation import canonical_commands, project_container_command, validate_project
from harness_kernel.language_adapters import get_language_profile


@pytest.mark.parametrize("language,marker,lockfile", [
    ("python", "pyproject.toml", "uv.lock"), ("c", "CMakeLists.txt", None),
    ("cpp", "CMakeLists.txt", None), ("rust", "Cargo.toml", "Cargo.lock"),
    ("javascript", "package.json", "package-lock.json"),
])
def test_all_profiles_have_fixed_project_gates(tmp_path: Path, language: str, marker: str,
                                               lockfile: str | None) -> None:
    (tmp_path / marker).write_text("{}" if marker.endswith("json") else "")
    if lockfile:
        (tmp_path / lockfile).write_text("")
    commands = canonical_commands(get_language_profile(language), tmp_path)
    assert commands
    assert all(isinstance(command, tuple) for _, command in commands)


def test_offline_dependency_without_lockfile_is_failure_not_skip(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'")
    result = validate_project(tmp_path, "rust", mode="local")
    assert not result.passed
    assert result.steps[0].status == "failed"
    assert "lockfile" in result.steps[0].stderr


def test_missing_tool_is_unavailable_not_passing(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text("")
    with patch("harness_kernel.project_validation.shutil.which", return_value=None):
        result = validate_project(tmp_path, "c", mode="local")
    assert not result.passed
    assert result.tier == "unavailable"
    assert result.steps[0].status == "unavailable"


def test_trusted_bundle_mount_is_read_only_and_network_disabled(tmp_path: Path) -> None:
    project = tmp_path / "project"; trusted = tmp_path / "trusted"
    project.mkdir(); trusted.mkdir()
    command = project_container_command(project, get_language_profile("python"), ("python3", "-V"),
                                        runtime="docker", trusted_tests=trusted, network_enabled=False)
    joined = " ".join(command)
    assert "--network none" in joined
    assert f"src={trusted.resolve()},dst=/trusted-tests,ro=true" in joined


def test_network_requires_distinct_approval(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}))
    (tmp_path / "package-lock.json").write_text("{}")
    with pytest.raises(PermissionError, match="separate approval"):
        validate_project(tmp_path, "javascript", network_enabled=True)


def test_local_steps_capture_failure_and_stop(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 2, b"", b"bad")
    with patch("harness_kernel.project_validation.shutil.which", return_value="/tool"):
        result = validate_project(tmp_path, "python", mode="local", runner=runner)
    assert not result.passed
    assert len(calls) == 1
    assert result.steps[0].stderr == "bad"


@pytest.mark.parametrize("language", ["python", "c", "cpp", "rust", "javascript"])
def test_multifile_fixture_is_detectable_and_has_sources(language: str) -> None:
    root = Path(__file__).parent / "fixtures" / "projects" / language
    profile = get_language_profile(language)
    assert any((root / marker).is_file() for marker in profile.project_markers)
    assert any(path.suffix in profile.suffixes for path in root.rglob("*"))
