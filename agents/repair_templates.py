from __future__ import annotations

import ast


SCORING_MATRIX_TEMPLATE = """\
def _score_value(value):
    return (
        (value < 0) * 1
        + (value == 0) * 2
        + (0 < value < 10) * 3
        + (10 <= value < 100) * 4
        + (value >= 100) * 5
    )


def analyze(matrix):
    return sum(_score_value(value) for row in matrix for value in row)
"""


TEMPLATES = {
    "scoring_matrix": SCORING_MATRIX_TEMPLATE,
}


def detect_scoring_matrix_pattern(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    if "analyze" not in function_names:
        return False

    has_nested_for = any(
        isinstance(node, ast.For) and any(isinstance(child, ast.For) for child in ast.walk(node) if child is not node)
        for node in ast.walk(tree)
    )
    constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)}
    scoring_constants = {0, 1, 2, 3, 4, 5, 10, 100}
    return has_nested_for and scoring_constants.issubset(constants)


def select_repair_template(source: str, forced_template: str | None = None) -> str:
    if forced_template:
        return forced_template if forced_template in TEMPLATES else ""
    if detect_scoring_matrix_pattern(source):
        return "scoring_matrix"
    return ""


def get_repair_template(template_name: str) -> str:
    return TEMPLATES[template_name]
