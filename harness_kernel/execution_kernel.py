from __future__ import annotations

from agents.generation_controller import GenerationController
from agents.base import AgentResult
from harness_kernel.task_ir import TaskIR


class ExecutionKernel:
    """Thin wrapper around the current generation controller.

    This names the stable execution boundary without rewriting the working
    controller. Future frontends can pass ``TaskIR`` into this wrapper while the
    existing validation and repair loop remains unchanged.
    """

    def __init__(self, controller: GenerationController | None = None) -> None:
        self.controller = controller or GenerationController()

    def run(self, task: TaskIR, initial_prompt: str) -> AgentResult:
        return self.controller.run(target=task.goal, initial_prompt=initial_prompt)
