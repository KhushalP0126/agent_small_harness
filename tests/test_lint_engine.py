import unittest

from engines.lint_engine import LintEngine


class LintEngineWildcardImportTests(unittest.TestCase):
    def test_wildcard_import_is_blocking_without_pylint(self) -> None:
        findings = LintEngine(executable=None).scan(
            "from pygame.locals import *\n\ndef main():\n    return KEYDOWN\n"
        )

        wildcard = [
            finding
            for finding in findings
            if finding.metrics.get("symbol") == "wildcard-import"
        ]
        self.assertEqual(len(wildcard), 1)
        self.assertEqual(wildcard[0].severity, "High")
        self.assertEqual(wildcard[0].diagnostic.violation, "WILDCARD_IMPORT")
        self.assertIn("qualify names", wildcard[0].diagnostic.recommended_refactor)

    def test_explicit_import_has_no_wildcard_finding(self) -> None:
        findings = LintEngine(executable=None).scan(
            "import pygame\n\ndef main():\n    return pygame.KEYDOWN\n"
        )

        self.assertFalse(
            any(
                finding.metrics.get("symbol") == "wildcard-import"
                for finding in findings
            )
        )


if __name__ == "__main__":
    unittest.main()
