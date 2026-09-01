"""Public integration imports must stay small and backwards compatible."""

import unittest


class PublicImportTests(unittest.TestCase):
    def test_preferred_routing_exports(self) -> None:
        from routing import Bridge, EventWriter, build_default_tool_registry

        self.assertTrue(callable(Bridge))
        self.assertTrue(callable(EventWriter))
        self.assertTrue(callable(build_default_tool_registry))

    def test_legacy_bridge_import_remains_available(self) -> None:
        from harness_kernel.tui_bridge import Bridge, EventWriter

        self.assertTrue(callable(Bridge))
        self.assertTrue(callable(EventWriter))


if __name__ == "__main__":
    unittest.main()
