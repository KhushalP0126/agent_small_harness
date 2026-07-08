import stat
import tempfile
import unittest
from pathlib import Path

from engines.bounds_engine import BoundsEngine
from engines.branching_engine import BranchingEngine
from engines.cost_engine import CostEngine
from engines.hazards_engine import HazardsEngine
from engines.lint_engine import LintEngine
from engines.math_engine import MathEngine
from engines.state_flow_engine import StateFlowEngine
from validation.policy import validate_findings


class MathEngineEdgeCaseTests(unittest.TestCase):
    def test_no_loop_code_reports_zero_depth(self) -> None:
        source = """
def normalize(value):
    return value.strip().lower()
"""
        finding = MathEngine().scan(source)[0]
        self.assertEqual(finding.metrics["max_loop_depth"], 0)
        self.assertEqual(finding.metrics["loop_types"], [])
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_for_while_nesting_at_policy_limit_is_compliant(self) -> None:
        source = """
def first_positive(rows):
    for row in rows:
        index = 0
        while index < len(row):
            if row[index] > 0:
                return row[index]
            index += 1
    return None
"""
        finding = MathEngine().scan(source)[0]
        self.assertEqual(finding.metrics["max_loop_depth"], 2)
        self.assertEqual(finding.metrics["loop_types"], ["for", "while"])
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_sequential_loops_are_not_treated_as_nested(self) -> None:
        source = """
def normalize(left, right):
    total = 0
    for value in left:
        total += value
    for value in right:
        total += value
    return total
"""
        finding = MathEngine().scan(source)[0]
        self.assertEqual(finding.metrics["max_loop_depth"], 1)
        self.assertTrue(validate_findings([finding]).is_compliant)


class BranchingEngineEdgeCaseTests(unittest.TestCase):
    def test_bool_ops_contribute_to_complexity(self) -> None:
        source = """
def allowed(user):
    if user and (user.active or user.admin):
        return True
    return False
"""
        finding = BranchingEngine().scan(source)[0]
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 4)
        self.assertEqual(finding.metrics["conditional_branch_count"], 1)

    def test_comprehension_filters_contribute_to_complexity(self) -> None:
        source = """
def bounded(values):
    return [value for value in values if value > 0 if value < 10]
"""
        finding = BranchingEngine().scan(source)[0]
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 3)
        self.assertEqual(finding.metrics["conditional_branch_count"], 0)

    def test_if_expression_contributes_to_complexity(self) -> None:
        source = """
def label(value):
    return "positive" if value > 0 else "other"
"""
        finding = BranchingEngine().scan(source)[0]
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 2)
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_dictionary_dispatch_is_not_treated_as_branch_chain(self) -> None:
        source = """
def label(status):
    labels = {
        "new": "New",
        "done": "Done",
        "blocked": "Blocked",
        "archived": "Archived",
    }
    return labels.get(status, "Unknown")
"""
        finding = BranchingEngine().scan(source)[0]
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 1)
        self.assertTrue(validate_findings([finding]).is_compliant)


class HazardsEngineEdgeCaseTests(unittest.TestCase):
    def test_local_container_mutation_is_not_module_state_hazard(self) -> None:
        source = """
def collect(values):
    items = []
    for value in values:
        items.append(value)
    return items
"""
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertNotIn("Module-level container mutation hazard", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)

    def test_annotated_module_container_subscript_mutation_is_hazard(self) -> None:
        source = """
STATE: dict = {}

def remember(key, value):
    STATE[key] = value
    return STATE
"""
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertIn("Module-level subscript mutation hazard", summaries)
        result = validate_findings(findings)
        self.assertFalse(result.is_compliant)
        self.assertIn("module_state_mutation", {violation.kind for violation in result.violations})

    def test_module_tuple_constant_is_not_mutable_state_hazard(self) -> None:
        source = """
LOOKUP = ("a", "b")

def contains(value):
    return value in LOOKUP
"""
        findings = HazardsEngine().scan(source)
        self.assertEqual(findings[0].summary, "No global mutation hazard detected")
        self.assertTrue(validate_findings(findings).is_compliant)

    def test_local_copy_of_module_mapping_can_be_mutated(self) -> None:
        source = """
DEFAULTS = {"mode": "safe"}

def with_override(key, value):
    result = dict(DEFAULTS)
    result[key] = value
    return result
"""
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertNotIn("Module-level subscript mutation hazard", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)

    def test_mutating_argument_container_is_not_module_state_hazard(self) -> None:
        source = """
def add_item(items, value):
    items.append(value)
    return items
"""
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertNotIn("Module-level container mutation hazard", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)


class CostEngineEdgeCaseTests(unittest.TestCase):
    def test_membership_outside_loop_is_not_hotspot(self) -> None:
        source = """
def contains_once(value, selected):
    return value in selected
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_membership_against_precomputed_set_inside_loop_is_not_hotspot(self) -> None:
        source = """
def filter_items(items, selected):
    selected_set = set(selected)
    result = []
    for item in items:
        if item in selected_set:
            result.append(item)
    return result
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_membership_against_unannotated_parameter_inside_loop_is_not_blocking(self) -> None:
        source = """
def filter_items(items, selected):
    result = []
    for item in items:
        if item in selected:
            result.append(item)
    return result
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_membership_against_annotated_list_parameter_inside_loop_is_hotspot(self) -> None:
        source = """
def filter_items(items, selected: list):
    result = []
    for item in items:
        if item in selected:
            result.append(item)
    return result
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Linear membership test inside loop")
        result = validate_findings([finding])
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.violations[0].kind, "algorithmic_cost")

    def test_string_membership_inside_loop_is_not_algorithmic_hotspot(self) -> None:
        source = """
def parse_lines(text):
    result = []
    for line in text.splitlines():
        if "=" not in line:
            continue
        result.append(line)
    return result
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_dict_membership_inside_loop_is_not_algorithmic_hotspot(self) -> None:
        source = """
def group_scores(records):
    team_to_players = {}
    for record in records:
        team = record["team"]
        if team not in team_to_players:
            team_to_players[team] = []
        team_to_players[team].append(record["player"])
    return team_to_players
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_literal_list_membership_inside_loop_is_hotspot(self) -> None:
        source = """
def filter_items(items):
    selected = ["a", "b", "c"]
    return [item for item in items if item in selected]
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Linear membership test inside loop")

    def test_live_failure_patterns_do_not_trigger_cost_engine(self) -> None:
        parse_source = """
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
        group_source = """
def group_top_scores(records):
    team_to_players = {}
    for record in records:
        team = record["team"]
        if team not in team_to_players:
            team_to_players[team] = []
        team_to_players[team].append(record["player"])
    return team_to_players
"""
        for source in (parse_source, group_source):
            finding = CostEngine().scan(source)[0]
            self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
            self.assertTrue(validate_findings([finding]).is_compliant)


class BoundsEngineEdgeCaseTests(unittest.TestCase):
    def test_direct_len_index_read_is_bounds_risk(self) -> None:
        source = """
def last_bad(items):
    return items[len(items)]
"""
        finding = BoundsEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Potential bounds risk")
        self.assertIn("items[len(items)]", finding.metrics["expressions"])

    def test_direct_len_index_write_is_bounds_risk(self) -> None:
        source = """
def write_bad(items, value):
    items[len(items)] = value
    return items
"""
        finding = BoundsEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Potential bounds risk")
        self.assertIn("items[len(items)]", finding.metrics["expressions"])
        self.assertIn("Potential out-of-bounds write", finding.metrics["risk_summaries"])

    def test_range_len_plus_one_is_bounds_risk(self) -> None:
        source = """
def copy_bad(items):
    result = []
    for index in range(len(items) + 1):
        result.append(items[index])
    return result
"""
        finding = BoundsEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Potential bounds risk")
        self.assertIn("Potential range upper-bound overflow", finding.metrics["risk_summaries"])

    def test_bounds_engine_is_advisory_by_default(self) -> None:
        source = """
def last_bad(items):
    return items[len(items)]
"""
        finding = BoundsEngine().scan(source)[0]
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_bounds_engine_can_be_policy_blocking(self) -> None:
        source = """
def last_bad(items):
    return items[len(items)]
"""
        finding = BoundsEngine().scan(source)[0]
        result = validate_findings([finding], {"allow_bounds_warnings": False})
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.violations[0].kind, "bounds_risk")
        self.assertEqual(result.violations[0].repair_hint, "guard_index_access")


class StateFlowEngineEdgeCaseTests(unittest.TestCase):
    def test_helper_that_updates_section_without_return_is_state_flow_risk(self) -> None:
        source = """
def process_line(line, section):
    if line.startswith("["):
        section = line.strip("[]")
    return None

def parse_sectioned_config(text):
    active_section = None
    for line in text.splitlines():
        process_line(line, active_section)
    return {}
"""
        finding = StateFlowEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Potential lost state update")
        self.assertEqual(finding.metrics["parameters"], ["section"])
        result = validate_findings([finding])
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.violations[0].kind, "state_flow_risk")
        self.assertEqual(result.violations[0].repair_hint, "return_updated_state")

    def test_helper_that_returns_updated_section_is_not_state_flow_risk(self) -> None:
        source = """
def process_line(line, section):
    if line.startswith("["):
        section = line.strip("[]")
    return section

def parse_sectioned_config(text):
    active_section = None
    for line in text.splitlines():
        active_section = process_line(line, active_section)
    return {}
"""
        finding = StateFlowEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No lost state-flow risk detected")
        self.assertTrue(validate_findings([finding]).is_compliant)


class LintEngineEdgeCaseTests(unittest.TestCase):
    def test_fake_pylint_fatal_becomes_lint_error_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pylint = Path(tmpdir) / "fake_pylint"
            fake_pylint.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps([{\n"
                "    'type': 'fatal',\n"
                "    'module': 'tmp',\n"
                "    'obj': '',\n"
                "    'line': 1,\n"
                "    'column': 0,\n"
                "    'path': 'tmp.py',\n"
                "    'symbol': 'parse-error',\n"
                "    'message': 'Unable to parse file',\n"
                "    'message-id': 'F0001'\n"
                "}]))\n",
                encoding="utf-8",
            )
            fake_pylint.chmod(fake_pylint.stat().st_mode | stat.S_IXUSR)
            finding = LintEngine(executable=str(fake_pylint)).scan("def broken(:\n")[0]
        self.assertEqual(finding.summary, "Pylint fatal")
        result = validate_findings([finding])
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.violations[0].kind, "lint_error")


if __name__ == "__main__":
    unittest.main()
