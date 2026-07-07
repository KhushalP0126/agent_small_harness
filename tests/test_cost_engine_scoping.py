import unittest

from engines.cost_engine import CostEngine
from validation.policy import validate_findings


class CostEngineScopingTests(unittest.TestCase):
    def test_sibling_function_container_names_do_not_leak(self) -> None:
        source = """def _dedupe_order(all_keys):
    seen = []
    for key in all_keys:
        if key not in seen:
            seen.append(key)
    return seen


def group_top_scores(records, seen):
    counts = {}
    for record in records:
        team = record["team"]
        if team in seen:
            counts[team] = counts.get(team, 0) + 1
    return counts
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Linear membership test inside loop")
        self.assertIn(4, finding.metrics["lines"])
        self.assertNotIn(13, finding.metrics["lines"])

    def test_parse_key_value_lines_with_helper_name_reuse_is_not_hotspot(self) -> None:
        source = """def _coerce_values(values):
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def parse_key_value_lines(text):
    parsed = {}
    for line in text.splitlines():
        if line.count("=") != 1:
            continue
        key, value = line.split("=")
        key = key.strip()
        if key:
            parsed[key] = value.strip()
    return parsed
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Linear membership test inside loop")
        self.assertEqual(finding.metrics["lines"], [4])
