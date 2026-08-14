from __future__ import annotations

import unittest
from pathlib import Path

from engines.decomposition_engine import DecompositionEngine
from engines.hazards_engine import HazardsEngine
from engines.import_extractors import extract_imports
from engines.library_registry import LibraryRegistry
from validation.policy import validate_findings

ROOT = Path(__file__).resolve().parents[1]
SNIPPETS = ROOT / "data" / "snippets"


class ImportExtractorTests(unittest.TestCase):
    def test_python_import_records(self) -> None:
        source = (SNIPPETS / "python" / "import_mixed_bindings.py").read_text(encoding="utf-8")
        records = extract_imports("python", source)
        names = {record.name for record in records}
        self.assertIn("pygame", names)
        self.assertIn("collections", names)
        ir = DecompositionEngine().decompose(source)
        self.assertEqual(len(ir.imports), len(records))
        self.assertTrue(all(item.kind == "module" for item in ir.imports))

    def test_c_header_records(self) -> None:
        source = (SNIPPETS / "c" / "import_headers.c").read_text(encoding="utf-8")
        records = extract_imports("c", source)
        names = [record.name for record in records]
        self.assertEqual(names, ["stdio.h", "local.h"])
        self.assertTrue(all(record.kind == "header" for record in records))

    def test_rust_use_records(self) -> None:
        source = (SNIPPETS / "rust" / "imports.rs").read_text(encoding="utf-8")
        records = extract_imports("rust", source)
        names = {record.name for record in records}
        self.assertTrue(any("std::process::Command" in name or name.startswith("std::process") for name in names))
        self.assertTrue(any(record.kind == "crate" for record in records))

    def test_javascript_import_and_require(self) -> None:
        source = (SNIPPETS / "javascript" / "imports.js").read_text(encoding="utf-8")
        records = extract_imports("javascript", source)
        names = {record.name for record in records}
        self.assertIn("fs", names)
        self.assertIn("child_process", names)
        kinds = {record.kind for record in records}
        self.assertTrue({"module", "require"} & kinds)


class ImportRiskCategoryTests(unittest.TestCase):
    def test_python_process_exec_hard_block(self) -> None:
        source = (SNIPPETS / "python" / "import_process_exec.py").read_text(encoding="utf-8")
        findings = HazardsEngine().scan(source)
        risk = [f for f in findings if f.metrics.get("risk_category") == "process_exec"]
        self.assertTrue(risk)
        self.assertEqual(risk[0].metrics["enforcement"], "hard_block")
        result = validate_findings(findings)
        self.assertFalse(result.is_compliant)
        self.assertTrue(any(v.kind == "import_risk_block" for v in result.violations))

    def test_python_dynamic_eval_hard_block(self) -> None:
        source = (SNIPPETS / "python" / "import_dynamic_eval.py").read_text(encoding="utf-8")
        findings = HazardsEngine().scan(source)
        self.assertTrue(any(f.metrics.get("risk_category") == "dynamic_eval" for f in findings))
        result = validate_findings(findings, behavior_verified=True)
        self.assertFalse(result.is_compliant)

    def test_python_network_advisory_does_not_block(self) -> None:
        source = (SNIPPETS / "python" / "import_network.py").read_text(encoding="utf-8")
        findings = HazardsEngine().scan(source)
        self.assertTrue(any(f.metrics.get("risk_category") == "network" for f in findings))
        result = validate_findings(findings)
        self.assertTrue(result.is_compliant)
        self.assertTrue(any(a.kind == "import_risk_advisory" for a in result.advisories))

    def test_python_filesystem_advisory(self) -> None:
        source = (SNIPPETS / "python" / "import_raw_filesystem.py").read_text(encoding="utf-8")
        findings = HazardsEngine().scan(source)
        self.assertTrue(any(f.metrics.get("risk_category") == "raw_filesystem" for f in findings))
        result = validate_findings(findings)
        self.assertTrue(result.is_compliant)
        self.assertTrue(result.advisories)

    def test_c_unsafe_maps_to_hard_block_category(self) -> None:
        from engines.treesitter_engine import TreeSitterHazardsEngine
        from engines import treesitter_support

        if not treesitter_support.is_available():
            self.skipTest("tree-sitter unavailable")
        source = (SNIPPETS / "c" / "unsafe.c").read_text(encoding="utf-8")
        findings = TreeSitterHazardsEngine("c").scan(source)
        categories = {f.metrics.get("risk_category") for f in findings}
        self.assertTrue(categories & {"unsafe_memory", "process_exec"})
        result = validate_findings(findings)
        self.assertFalse(result.is_compliant)

    def test_rust_and_javascript_risk_rules_cover_unsafe_constructs_and_calls(self) -> None:
        from engines.import_risk import match_call_risks, match_construct_risks

        rust = match_construct_risks("rust", [("unsafe", 3)])
        javascript = match_call_risks("javascript", [("eval", 2), ("Function", 4)])
        self.assertEqual(rust[0].category, "unsafe_memory")
        self.assertTrue(all(hit.category == "dynamic_eval" for hit in javascript))


class LibraryRegistryMultiLanguageTests(unittest.TestCase):
    def test_nested_python_schema_loads(self) -> None:
        registry = LibraryRegistry()
        self.assertTrue(registry.is_registered("pygame"))
        self.assertTrue(registry.is_registered("pygame", language="python"))
        self.assertFalse(registry.is_registered("pygame", language="rust"))
        schema = registry.get("pandas", language="python")
        self.assertIsNotNone(schema)
        self.assertIn("DataFrame", schema.allowed_calls)

    def test_legacy_flat_schema_still_loads(self) -> None:
        import json
        import tempfile

        payload = {
            "schema_version": "1.0",
            "libraries": {
                "demo": {
                    "allowed_calls": ["run"],
                    "context": "demo",
                    "unknown_api_repair": "use run",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            registry = LibraryRegistry(path)
            self.assertTrue(registry.is_registered("demo", language="python"))
            self.assertEqual(registry.libraries("python"), {"demo"})


class SerializationIncludesAdvisories(unittest.TestCase):
    def test_serialize_advisories(self) -> None:
        from validation.policy import serialize_validation_result

        source = (SNIPPETS / "python" / "import_network.py").read_text(encoding="utf-8")
        result = validate_findings(HazardsEngine().scan(source))
        payload = serialize_validation_result(result)
        self.assertIn("advisories", payload)
        self.assertTrue(payload["advisories"])


if __name__ == "__main__":
    unittest.main()
