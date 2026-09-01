import json
import sys
import unittest

from harness_kernel.extensions import (
    ExtensionAdapter, ExtensionManifest, ExtensionRequest, register_extension_tools,
)
from harness_kernel.governance import PermissionEvaluator, PermissionMode
from harness_kernel.tool_registry import ToolRegistry


class ExtensionTests(unittest.TestCase):
    def test_manifest_validation_and_capability_denial(self):
        with self.assertRaises(ValueError): ExtensionManifest.from_dict({})
        manifest = ExtensionManifest.from_dict({"name":"x","version":"1","command":sys.executable,"capabilities":["read"]})
        result = ExtensionAdapter(PermissionEvaluator()).invoke(manifest, {"capabilities":["network"]})
        self.assertEqual(result["kind"], "capability_denied")

    def test_malformed_response_is_structured(self):
        manifest = ExtensionManifest.from_dict({"name":"x","version":"1","command":sys.executable,"arguments":["-c", "print('bad')"]})
        result = ExtensionAdapter(PermissionEvaluator()).invoke(manifest, {})
        self.assertEqual(result["kind"], "malformed_response")

    def test_timeout_is_structured(self):
        manifest = ExtensionManifest.from_dict({"name":"x","version":"1","command":sys.executable,"arguments":["-c", "import time; time.sleep(1)"],"timeout":.01})
        result = ExtensionAdapter(PermissionEvaluator()).invoke(manifest, {})
        self.assertEqual(result["kind"], "timeout")

    def test_output_limit_is_enforced_without_returning_output(self):
        manifest = ExtensionManifest.from_dict({"name":"x","version":"1","command":sys.executable,"arguments":["-c", "print('x' * 10000)"],"maximum_output":64})
        result = ExtensionAdapter(PermissionEvaluator()).invoke(manifest, {})
        self.assertEqual(result["kind"], "output_limit_exceeded")

    def test_manifest_registers_through_typed_tool_registry(self):
        registry = ToolRegistry()
        manifest = ExtensionManifest.from_dict({"name":"echo","version":"1","command":sys.executable,"arguments":["-c", "import json,sys; print(json.dumps({'value': 1}))"]})
        register_extension_tools(registry, [manifest], PermissionEvaluator())
        result = registry.dispatch("extension.echo", ExtensionRequest({"input": 1}))
        self.assertTrue(result.ok)
        self.assertTrue(result.value.ok)


if __name__ == "__main__": unittest.main()
