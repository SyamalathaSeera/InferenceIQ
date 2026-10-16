from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.inference.simulator import HistoricalSample, simulate
from app.schemas.simulation import SimulationRequest


class PolicyWorkflowState(TypedDict, total=False):
    policy_id: str
    name: str
    version: int
    policy: str
    require_zero_data_retention: bool
    samples: list[dict[str, Any]]
    simulation: dict[str, Any]
    validation_errors: list[str]
    approved: bool
    status: str


def validate_proposal(state: PolicyWorkflowState) -> dict[str, Any]:
    errors: list[str] = []
    if not state.get("samples"):
        errors.append("No automatic inference history is available for simulation.")
    return {
        "validation_errors": errors,
        "status": "invalid" if errors else "validated",
    }


def route_after_validation(state: PolicyWorkflowState) -> str:
    return "stop" if state["validation_errors"] else "simulate"


def run_simulation(state: PolicyWorkflowState) -> dict[str, Any]:
    samples = [HistoricalSample(**item) for item in state["samples"]]
    result = simulate(
        samples,
        SimulationRequest(
            policy=state["policy"],
            require_zero_data_retention=state["require_zero_data_retention"],
            limit=len(samples),
        ),
    )
    if result.sample_size == 0:
        return {
            "simulation": result.model_dump(mode="json"),
            "validation_errors": [
                (
                    "Historical records exist, but none contain replayable candidate "
                    "evidence."
                )
            ],
            "status": "invalid",
        }
    return {
        "simulation": result.model_dump(mode="json"),
        "status": "pending_approval",
    }


def route_after_simulation(state: PolicyWorkflowState) -> str:
    return "stop" if state["status"] == "invalid" else "review"


def await_human_review(state: PolicyWorkflowState) -> dict[str, Any]:
    approved = interrupt(
        {
            "policy_id": state["policy_id"],
            "name": state["name"],
            "version": state["version"],
            "simulation": state["simulation"],
            "question": "Promote this policy version to canary?",
        }
    )
    return {"approved": bool(approved)}


def finalize_review(state: PolicyWorkflowState) -> dict[str, str]:
    return {"status": "canary" if state["approved"] else "rejected"}


def create_policy_graph(checkpointer: Any) -> Any:
    builder = StateGraph(PolicyWorkflowState)
    builder.add_node("validate", validate_proposal)
    builder.add_node("simulate", run_simulation)
    builder.add_node("review", await_human_review)
    builder.add_node("finalize", finalize_review)
    builder.add_edge(START, "validate")
    builder.add_conditional_edges(
        "validate", route_after_validation, {"simulate": "simulate", "stop": END}
    )
    builder.add_conditional_edges(
        "simulate", route_after_simulation, {"review": "review", "stop": END}
    )
    builder.add_edge("review", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
