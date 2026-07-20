import tempfile
import unittest
from pathlib import Path

from agents.plan_mode import PlanModeAgent
from agents.repo_map_agent import RepoMapAgent


ALPHA_SOURCE = """
import os
from pkg import beta
import totally_made_up_pkg

GLOBAL_CONFIG = {}


def alpha_entry(data):
    total = 0
    for row in data:
        for value in row:
            total += value
    return beta.helper(total)


class Widget:
    def __init__(self, size):
        self.size = size
"""

BETA_SOURCE = """
def helper(x):
    return x + 1
"""

BROKEN_SOURCE = "def broken(:\n"


class RepoMapAgentTests(unittest.TestCase):
    def _build_fixture(self, root: Path) -> None:
        package = root / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "alpha.py").write_text(ALPHA_SOURCE, encoding="utf-8")
        (package / "beta.py").write_text(BETA_SOURCE, encoding="utf-8")
        (root / "broken.py").write_text(BROKEN_SOURCE, encoding="utf-8")

    def test_extracts_functions_calls_returns_and_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fixture(root)
            graph = RepoMapAgent().map_repo(root)

            alpha = next(record for record in graph.files if record.module == "pkg.alpha")
            function = next(fn for fn in alpha.functions if fn.name == "alpha_entry")
            self.assertEqual(function.args, ["data"])
            self.assertIn("beta.helper", function.calls)
            self.assertEqual(function.returns, "value")
            self.assertIn("Widget", alpha.classes)
            self.assertIn("GLOBAL_CONFIG", alpha.module_vars)
            self.assertIn("size", alpha.instance_vars)
            self.assertEqual(alpha.max_loop_depth, 2)

    def test_classifies_import_origins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fixture(root)
            graph = RepoMapAgent().map_repo(root)

            alpha = next(record for record in graph.files if record.module == "pkg.alpha")
            by_module = {imp.module: imp.kind for imp in alpha.imports}
            self.assertEqual(by_module["os"], "stdlib")
            self.assertEqual(by_module["pkg"], "local")
            self.assertEqual(by_module["totally_made_up_pkg"], "third_party")

    def test_unparseable_file_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fixture(root)
            graph = RepoMapAgent().map_repo(root)

            broken = next(record for record in graph.files if record.path == "broken.py")
            self.assertTrue(broken.parse_error)
            analyzed = {record.module for record in graph.files if not record.parse_error}
            self.assertIn("pkg.alpha", analyzed)
            self.assertIn("pkg.beta", analyzed)

    def test_renderings_expose_functions_and_local_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fixture(root)
            agent = RepoMapAgent()
            graph = agent.map_repo(root)

            context = agent.to_plan_context(graph)
            self.assertTrue(context[0].startswith("REPO MAP"))
            self.assertTrue(any("alpha_entry" in line for line in context))
            self.assertTrue(any("pkg.alpha -> pkg.beta" in line for line in context))

            mermaid = agent.to_mermaid(graph)
            self.assertTrue(mermaid.startswith("flowchart LR"))
            self.assertIn("-->", mermaid)

    def test_plan_mode_merge_is_opt_in(self) -> None:
        prompt = "Write a helper that adds two numbers."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fixture(root)

            without_repo = PlanModeAgent().plan(prompt)
            with_repo = PlanModeAgent().plan(prompt, repo_root=root)

            self.assertFalse(
                any(line.startswith("REPO MAP") for line in without_repo.dependency_graph_context)
            )
            self.assertTrue(
                any(line.startswith("REPO MAP") for line in with_repo.dependency_graph_context)
            )
            # Prompt-only context is preserved as the prefix; repo lines are appended.
            prefix = with_repo.dependency_graph_context[: len(without_repo.dependency_graph_context)]
            self.assertEqual(prefix, without_repo.dependency_graph_context)


if __name__ == "__main__":
    unittest.main()
