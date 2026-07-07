import unittest

from agents.plan_mode import PlanModeAgent


class GraphGroundedContextTests(unittest.TestCase):
    def test_stateful_task_worker_packet_includes_dependency_graph_context(self) -> None:
        prompt = """
        Write a Python function named process_events(events).
        It receives ordered event dicts with type and value fields.
        Maintain a running state where reset sets the total to 0,
        add increases the total, subtract decreases the total, and emit appends
        the current total to the returned list.
        process_events([{'type': 'add', 'value': 3}, {'type': 'emit'}, {'type': 'reset'}, {'type': 'emit'}]) == [3, 0]
        """

        plan = PlanModeAgent().plan(prompt)
        packet = PlanModeAgent().to_worker_packet(plan)

        self.assertIn("FUNCTION: process_events", packet)
        self.assertIn("DEPENDENCY GRAPH:", packet)
        self.assertIn("events -> state transitions -> emitted totals", packet)
        self.assertIn("reset -> total = 0", packet)
        self.assertIn("emit -> append current total", packet)


if __name__ == "__main__":
    unittest.main()
