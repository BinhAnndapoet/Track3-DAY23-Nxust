"""Checkpointer wiring and thread isolation tests."""

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import Route, Scenario, initial_state


def test_memory_checkpointer_keeps_history_per_thread() -> None:
    checkpointer = build_checkpointer("memory")
    graph = build_graph(checkpointer=checkpointer)
    scenario = Scenario(id="persistence-a", query="status", expected_route=Route.SIMPLE)
    state = initial_state(scenario)
    first_config = {"configurable": {"thread_id": state["thread_id"]}}
    second_config = {"configurable": {"thread_id": "thread-persistence-b"}}

    graph.update_state(first_config, state)

    saved_state = graph.get_state(first_config).values
    first_history = list(graph.get_state_history(first_config))
    second_history = list(graph.get_state_history(second_config))

    assert saved_state["thread_id"] == state["thread_id"]
    assert first_history
    assert not second_history
