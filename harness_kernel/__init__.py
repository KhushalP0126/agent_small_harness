"""Lazy package exports.

Importing ``harness_kernel`` must not eagerly import the execution kernel:
the generation controller also imports kernel submodules for typed tool
dispatch, and eager package initialization would create a circular import.
"""

from harness_kernel.function_contracts import ContractQueue, DealExample, FunctionContract, parse_contract_queue_json
from harness_kernel.task_ir import TaskIR, TemplateRoute, ValidationPlan

__all__ = [
    "ContractQueue",
    "DealExample",
    "ExecutionKernel",
    "FunctionContract",
    "TaskIR",
    "TemplateRoute",
    "ValidationPlan",
    "parse_contract_queue_json",
]


def __getattr__(name: str):
    if name == "ExecutionKernel":
        from harness_kernel.execution_kernel import ExecutionKernel

        return ExecutionKernel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
