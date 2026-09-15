from typing import Any, TypedDict

from .contracts import Scenario
from .scenarios import run_scenario


class LabState(TypedDict, total=False):
    scenario: str
    parameters: dict[str, Any]
    result: dict[str, Any]


def build_graph():
    """Build a LangGraph when installed; fallback keeps local simulation usable."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None
    graph = StateGraph(LabState)
    def execute(state: LabState) -> LabState:
        state["result"] = run_scenario(Scenario(state["scenario"]), state.get("parameters", {}))
        return state
    graph.add_node("execute_scenario", execute)
    graph.set_entry_point("execute_scenario")
    graph.add_edge("execute_scenario", END)
    return graph.compile()


def invoke(scenario: Scenario, parameters: dict[str, Any]) -> dict[str, Any]:
    graph = build_graph()
    if graph:
        return graph.invoke({"scenario": scenario.value, "parameters": parameters})["result"]
    return run_scenario(scenario, parameters)
