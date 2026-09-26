import unittest

from agents.peer_consultation import (
    bounded_peer_memo,
    bounded_source_excerpt,
    build_peer_consultation_prompt,
    is_advisory_memo,
)


class PeerConsultationTests(unittest.TestCase):
    def test_packet_is_bounded_and_advisory(self) -> None:
        prompt = build_peer_consultation_prompt(
            target="repair parser",
            attempt=2,
            failure_signature="parse_error:parser.py",
            violation={"kind": "parse_error", "location": "parser.py", "summary": "invalid syntax"},
            diagnostic_deltas=[],
            source="x" * 10_000,
        )
        self.assertIn("ADVICE ONLY", prompt)
        self.assertIn("excerpt truncated", prompt)
        self.assertLess(len(prompt), 5_000)

    def test_packet_and_memo_redact_obvious_credentials(self) -> None:
        prompt = build_peer_consultation_prompt(
            target="token=abc123",
            attempt=1,
            failure_signature="",
            violation={},
            diagnostic_deltas=[],
            source="api_key=abc123\nBearer abc123",
        )
        self.assertNotIn("abc123", prompt)
        self.assertEqual(bounded_peer_memo("password: hidden"), "password=[REDACTED]")

    def test_source_excerpt_keeps_small_source_unchanged(self) -> None:
        self.assertEqual(bounded_source_excerpt("def f():\n    return 1\n"), "def f():\n    return 1\n")

    def test_code_is_not_an_advisory_memo(self) -> None:
        self.assertFalse(is_advisory_memo("def unsafe():\n    return 1"))
        self.assertFalse(is_advisory_memo("```python\ndef unsafe(): pass\n```"))
        self.assertTrue(is_advisory_memo("Option 1: inspect the parsing boundary before retrying."))


if __name__ == "__main__":
    unittest.main()
