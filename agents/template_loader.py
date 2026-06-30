from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATES_ROOT = ROOT / "templates"

LANGUAGE_EXTENSIONS = {
    "python": "py",
    "c": "c",
    "cpp": "cpp",
}


class TemplateLibrary:
    """Loads skeletal generation seeds from ``templates/<task>/<language>/<task>.<ext>``."""

    def __init__(self, root: Path | str = DEFAULT_TEMPLATES_ROOT) -> None:
        self.root = Path(root)

    def path_for(self, task: str, language: str) -> Path:
        language = language.strip().lower()
        ext = LANGUAGE_EXTENSIONS.get(language, language)
        return self.root / task / language / f"{task}.{ext}"

    def load(self, task: str, language: str) -> str | None:
        path = self.path_for(task, language)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def available(self, task: str) -> list[str]:
        task_dir = self.root / task
        if not task_dir.is_dir():
            return []
        return sorted(child.name for child in task_dir.iterdir() if child.is_dir())

    def tasks(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(child.name for child in self.root.iterdir() if child.is_dir())
