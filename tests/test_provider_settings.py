import os
import unittest
from unittest.mock import patch

from harness_kernel.contribution import ApiCostGuard, ContributionSplit, ContributionTelemetry, WeightedSchedule
from harness_kernel.governance import PermissionEvaluator, PermissionMode
from harness_kernel.provider_settings import (
    Provider, ProviderSettings, SessionCredentialStore, credential_metadata,
    redact_secret, resolve_credential,
)
from agents.generation_controller import GenerationController


class ProviderSettingsTests(unittest.TestCase):
    def test_endpoint_guards(self):
        ProviderSettings(Provider.DEEPSEEK, "https://api.example.com/v1", "model").validate()
        for endpoint in ("http://api.example.com", "https://key@api.example.com"):
            with self.assertRaises(ValueError):
                ProviderSettings(Provider.DEEPSEEK, endpoint, "model").validate()
        with self.assertRaises(ValueError):
            ProviderSettings(Provider.QWEN, "file://localhost/tmp", "qwen", local_development_confirmed=True).validate()
        with self.assertRaises(ValueError):
            ProviderSettings(Provider.QWEN, "http://localhost:11434", "qwen").validate()
        ProviderSettings(Provider.QWEN, "http://localhost:11434", "qwen", local_development_confirmed=True).validate()

    def test_credential_precedence_and_metadata_without_raw_secret(self):
        os_store, session = SessionCredentialStore(), SessionCredentialStore()
        os_store.set("deepseek", "stored-value")
        session.set("deepseek", "session-value")
        value, source = resolve_credential(Provider.DEEPSEEK, environment={"DEEPSEEK_API_KEY": "env-value"}, store=os_store, dotenv={"DEEPSEEK_API_KEY": "dot-value"}, session=session)
        self.assertEqual((value, source), ("env-value", "environment"))
        value, source = resolve_credential(Provider.DEEPSEEK, environment={}, store=os_store, dotenv={"DEEPSEEK_API_KEY": "dot-value"}, session=session)
        self.assertEqual((value, source), ("stored-value", "credential_store"))
        metadata = credential_metadata(value)
        self.assertNotIn(value, repr(metadata))
        self.assertEqual(metadata["last_four"], "alue")
        self.assertEqual(redact_secret(f"failed for {value}", [value]), "failed for [REDACTED]")

    def test_session_clear(self):
        store = SessionCredentialStore(); store.set("deepseek", "secret"); store.clear("deepseek")
        self.assertIsNone(store.get("deepseek"))


class ContributionTests(unittest.TestCase):
    def test_split_and_schedule_are_valid_and_reproducible(self):
        with self.assertRaises(ValueError): ContributionSplit(60, 50)
        first = WeightedSchedule(ContributionSplit(50, 50), "session-a").sequence(100)
        self.assertEqual(first, WeightedSchedule(ContributionSplit(), "session-a").sequence(100))
        self.assertEqual(first.count("qwen"), 50)

    def test_deviations_require_explicit_reason(self):
        telemetry = ContributionTelemetry(ContributionSplit())
        with self.assertRaises(ValueError): telemetry.record("api", "qwen")
        telemetry.record("api", "qwen", "user approved fallback")
        self.assertTrue(telemetry.snapshot()["mixed_fallback_routed"])

    def test_cost_guard_pauses_at_cap(self):
        guard = ApiCostGuard(1.0, .9)
        self.assertEqual(guard.decision(.2), "approval_required")
        self.assertEqual(guard.decision(.2, approved_overage=True), "allowed")

    def test_permission_modes(self):
        self.assertFalse(PermissionEvaluator(PermissionMode.PLAN).evaluate("write", "edit").allowed)
        self.assertTrue(PermissionEvaluator(PermissionMode.ACCEPT_EDITS).evaluate("write", "edit").allowed)
        self.assertFalse(PermissionEvaluator(PermissionMode.DONT_ASK).evaluate("read", "unknown").allowed)

    def test_controller_routes_initial_generation_and_records_contribution(self):
        calls = []
        source = "def identity(value):\n    return value\n"
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: calls.append("qwen") or source,
            repair_supplier=lambda _draft, _prompt: source,
            architect_supplier=lambda _draft, _prompt: calls.append("api") or source,
            contribution_split=ContributionSplit(0, 100),
            enable_history_context=False,
            session_id="routing-test",
        )
        result = controller.run("identity", "build identity")
        self.assertEqual(calls, ["api"])
        self.assertEqual(result.payload["contribution"]["counts"]["api"], 1)


if __name__ == "__main__": unittest.main()
