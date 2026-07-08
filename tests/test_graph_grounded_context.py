import unittest

from agents.generation_controller import GenerationController
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

    def test_small_worker_repair_prompt_preserves_dependency_graph_context(self) -> None:
        prompt = """
        Write a Python function named process_events(events).
        It receives ordered event dicts with type and value fields.
        Maintain a running state where reset sets the total to 0,
        add increases the total, subtract decreases the total, and emit appends
        the current total to the returned list.
        process_events([{'type': 'add', 'value': 3}, {'type': 'emit'}, {'type': 'reset'}, {'type': 'emit'}]) == [3, 0]
        """
        plan = PlanModeAgent().plan(prompt)
        initial_prompt = PlanModeAgent().to_worker_packet(plan)
        source = """
def process_events(events):
    output = []
    total = 0
    for event in events:
        if event.get("type") == "reset":
            total = 0
        elif event.get("type") == "add":
            total += event.get("value", 0)
        elif event.get("type") == "subtract":
            total -= event.get("value", 0)
        elif event.get("type") == "emit":
            output.append(total)
    return output
"""
        captured_prompts: list[str] = []

        def repair_supplier(draft: str, retry_prompt: str) -> str:
            captured_prompts.append(retry_prompt)
            return draft

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: source,
            repair_supplier=repair_supplier,
            policy={"max_cyclomatic_complexity": 3},
        )
        controller.run(target="state graph repair", initial_prompt=initial_prompt)

        self.assertEqual(len(captured_prompts), 1)
        retry_prompt = captured_prompts[0]
        self.assertIn("PRESERVE CONTEXT:", retry_prompt)
        self.assertIn("DEPENDENCY GRAPH:", retry_prompt)
        self.assertIn("events -> state transitions -> emitted totals", retry_prompt)
        self.assertIn("emit -> append current total", retry_prompt)
        self.assertIn("do not simplify the code in a way that changes the preserved semantics", retry_prompt)


if __name__ == "__main__":
    unittest.main()
