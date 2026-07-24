import unittest

from agents.generation_controller import GenerationController
from prompt.budget import budget_prompt
from prompt.summarizer import DefaultPromptSummarizer


class PromptSummarizerTests(unittest.TestCase):
    def test_default_summarizer_collapses_attempt_history(self) -> None:
        history = "\n".join(
            [
                f"- Attempt {index}:\n"
                "  Static failure: cyclomatic_complexity had 9; required 7.\n"
                "  Behavior failure: basic expected 5 but got -1 (wrong return)."
                for index in range(12)
            ]
        )
        summary = DefaultPromptSummarizer(max_attempts=3)(history)

        self.assertIn("9 older attempt(s) omitted", summary)
        self.assertIn("Attempt 9:", summary)
        self.assertIn("Attempt 11:", summary)
        self.assertNotIn("Attempt 0:", summary)

    def test_budget_only_summarizes_prior_attempt_section(self) -> None:
        history = "\n".join(
            [
                f"- Attempt {index}:\n"
                f"  Static failure: branch_count had {20 + index}; required 7.\n"
                + ("  Previous draft: " + ("x" * 120))
                for index in range(16)
            ]
        )
        diagnostics = (
            "DIAGNOSTIC DELTAS:\n"
            "- cyclomatic_complexity: prior 12 -> current 11 (improved by 1).\n"
            "A repeated violation with no improvement requires a stronger structural rewrite."
        )
        text = (
            "CURRENT DRAFT:\ndef analyze(value):\n    return value\n\n"
            f"PRIOR FAILED ATTEMPTS:\n{history}\n\n{diagnostics}"
        )

        result = budget_prompt(
            text,
            max_chars=1800,
            summarizer=DefaultPromptSummarizer(max_attempts=4),
        )

        self.assertEqual(
            result.strategy,
            "summarize_prior_attempts_preserve_diagnostics",
        )
        self.assertIn("12 older attempt(s) omitted", result.text)
        self.assertIn(diagnostics, result.text)
        self.assertIn("CURRENT DRAFT:", result.text)

    def test_controller_constructs_default_summarizer(self) -> None:
        controller = GenerationController(max_retries=0)
        self.assertIsInstance(
            controller.prompt_summarizer,
            DefaultPromptSummarizer,
        )


if __name__ == "__main__":
    unittest.main()
