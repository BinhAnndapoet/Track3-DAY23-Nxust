"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel

from .llm import get_llm
from .state import AgentState, make_event


# ─── Classification schema ────────────────────────────────────────────

class IntentClassification(BaseModel):
    """Structured output for route classification."""

    route: Literal["simple", "tool", "missing_info", "risky", "error"]
    risk_level: Literal["high", "low"]
    reasoning: str  # brief justification for audit trail


# ─── EXAMPLE: working node (provided for reference) ──────────────────

def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── 1. classify: LLM-based route + risk classification ──────────────

def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM with structured output.

    Priority: risky > tool > missing_info > error > simple
    """
    query = state.get("query", "")

    # Build classification prompt
    priority_guide = (
        "risky: actions requiring human approval (refunds, deletions, financial ops)\n"
        "tool: requests needing external data lookup or computation\n"
        "missing_info: vague or incomplete queries needing clarification\n"
        "error: system failures, timeouts, technical issues\n"
        "simple: straightforward informational requests"
    )
    prompt = (
        f"Classify this support request:\n\nQuery: {query}\n\n"
        f"Priority order (apply highest applicable):\n{priority_guide}\n\n"
        "Return JSON matching the schema exactly. The route MUST be one of the exact values "
        "simple, tool, missing_info, risky, or error; never invent a label or phrase."
    )

    # LLM call with structured output
    route = "simple"
    risk_level = "low"
    reasoning = "fallback: LLM unavailable"

    try:
        llm = get_llm()
        structured_llm = llm.with_structured_output(IntentClassification)
        decision = structured_llm.invoke(prompt)

        # Validate route is one of allowed values
        allowed = {"simple", "tool", "missing_info", "risky", "error"}
        if decision.route in allowed:
            route = decision.route
            risk_level = decision.risk_level if decision.risk_level in {"high", "low"} else "low"
            reasoning = decision.reasoning
        else:
            reasoning = f"invalid route from LLM: {decision.route}, falling back to simple"

    except Exception as exc:
        reasoning = f"LLM failed: {type(exc).__name__}, falling back to simple"
        return {
            "route": "simple",
            "risk_level": "low",
            "errors": [f"classify: {exc}"],
            "events": [make_event("classify", "failed", reasoning, route="simple", fallback=True)],
        }

    return {
        "route": route,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"route={route}, risk={risk_level}", reasoning=reasoning)],
    }


# ─── 2. tool: mock execution with error simulation ─────────────────────

def tool_node(state: AgentState) -> dict:
    """Execute mock tool based on route and attempt count.

    - error route + attempt < 2: return ERROR result (simulates transient failure)
    - risky route: proceed with approved action
    - other routes: return mock success
    """
    route = state.get("route", "")
    attempt = state.get("attempt", 0)
    query = state.get("query", "")
    approval = state.get("approval", {})
    proposed_action = state.get("proposed_action", "")

    # Simulate transient error for error-route retry testing
    if route == "error" and attempt < 2:
        result = f"ERROR: transient failure on attempt {attempt}"
        event_type = "failed"
        metadata = {"attempt": attempt, "simulated": True}
    else:
        # Build context-aware result
        context = query
        if route == "risky" and approval.get("approved"):
            context = f"{query} [APPROVED: {approval.get('comment', '')}]"
        elif proposed_action:
            context = f"{query} [PROPOSED: {proposed_action}]"

        result = f"SUCCESS: processed '{context[:50]}' on attempt {attempt}"
        event_type = "completed"
        metadata = {"attempt": attempt, "simulated": True}

    return {
        "tool_results": [result],
        "events": [make_event("tool", event_type, result, **metadata)],
    }


# ─── 3. evaluate: retry gate — check tool result ──────────────────────

def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool result: needs_retry if ERROR present, else success."""
    tool_results = state.get("tool_results", [])

    # Read the latest entry
    if not tool_results:
        evaluation_result = "needs_retry"
        reason = "no tool results to evaluate"
    else:
        latest = tool_results[-1]
        if "ERROR" in latest:
            evaluation_result = "needs_retry"
            reason = "error marker detected in tool result"
        else:
            evaluation_result = "success"
            reason = "tool result satisfactory"

    return {
        "evaluation_result": evaluation_result,
        "events": [make_event("evaluate", "completed", f"verdict={evaluation_result}", reason=reason)],
    }


# ─── 4. answer: LLM-grounded final response ──────────────────────────

def answer_node(state: AgentState) -> dict:
    """Generate final response grounded in available context."""
    query = state.get("query", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval", {})
    proposed_action = state.get("proposed_action", "")

    # Build context from available data
    context_parts = [f"Query: {query}"]

    # Add tool results if any
    relevant_results = [r for r in tool_results if "ERROR" not in r]
    if relevant_results:
        context_parts.append(f"Tool Results:\n" + "\n".join(f"- {r}" for r in relevant_results))

    # Add approval context for risky routes
    if approval.get("approved"):
        context_parts.append(f"Action Approved: {proposed_action or 'user action'} (reviewed by {approval.get('reviewer', 'system')})")
    elif approval:
        context_parts.append("Action was not approved.")

    context = "\n\n".join(context_parts)

    prompt = (
        f"Based on the following context, generate a helpful and accurate response.\n\n"
        f"{context}\n\n"
        "If the context lacks sufficient information, acknowledge the limitation clearly."
    )

    try:
        llm = get_llm(temperature=0.3)
        response = llm.invoke(prompt)
        final_answer = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        final_answer = f"Unable to generate response: {type(exc).__name__}"
        return {
            "final_answer": final_answer,
            "errors": [f"answer: {exc}"],
            "events": [make_event("answer", "failed", str(exc)[:100])],
        }

    return {
        "final_answer": final_answer,
        "events": [make_event("answer", "completed", "grounded generation done")],
    }


# ─── 5. clarify: ask for missing information ──────────────────────────

def ask_clarification_node(state: AgentState) -> dict:
    """Generate a specific clarification question based on the query."""
    query = state.get("query", "")
    approval = state.get("approval", {})
    proposed_action = state.get("proposed_action", "")

    # Determine reason for clarification
    if approval.get("approved") is False:
        reason = "action rejected"
        context = f"Previous action proposal '{proposed_action}' was rejected. "
    else:
        reason = "missing information"
        context = ""

    prompt = (
        f"Generate ONE specific clarification question to gather missing information.\n\n"
        f"Original query: {query}\n"
        f"{context}"
        "The question should be actionable and directly address what's missing."
    )

    try:
        llm = get_llm(temperature=0.3)
        response = llm.invoke(prompt)
        pending_question = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        pending_question = f"Can you provide more details about: {query}?"
        reason = f"LLM failed: {type(exc).__name__}, used generic question"

    # Also set final_answer so metrics consider this a completed route
    final_answer = f"Clarification needed: {pending_question}"

    return {
        "pending_question": pending_question,
        "final_answer": final_answer,
        "events": [make_event("clarify", "completed", f"clarification requested: {reason}")],
    }


# ─── 6. risky_action: prepare action for approval ─────────────────────

def risky_action_node(state: AgentState) -> dict:
    """Describe the proposed risky action requiring human approval."""
    query = state.get("query", "")
    risk_level = state.get("risk_level", "high")

    prompt = (
        f"Describe the specific action this request requires and why it needs human approval.\n\n"
        f"Request: {query}\n"
        "Focus on: what will be done, potential impact, and why review is needed."
    )

    try:
        llm = get_llm(temperature=0.3)
        response = llm.invoke(prompt)
        proposed_action = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        proposed_action = f"Action required for: {query[:50]}..."
        return {
            "proposed_action": proposed_action,
            "errors": [f"risky_action: {exc}"],
            "events": [make_event("risky_action", "completed", "action proposed (LLM failed, used fallback)")],
        }

    return {
        "proposed_action": proposed_action,
        "events": [make_event("risky_action", "completed", "action proposed for review", risk_level=risk_level)],
    }


# ─── 7. approval: human-in-the-loop (mock by default) ─────────────────

def approval_node(state: AgentState) -> dict:
    """Mock approval decision. Set LANGGRAPH_INTERRUPT=true for real HITL."""
    proposed_action = state.get("proposed_action", "")

    # Check for real HITL mode
    import os
    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt
        interrupt(f"Approve action? {proposed_action[:100]}")

    # Mock approval for CI/testing
    approval = {
        "approved": True,
        "reviewer": "mock-reviewer",
        "comment": "auto-approved for testing",
    }

    return {
        "approval": approval,
        "events": [make_event("approval", "completed", f"approved={approval['approved']}")],
    }


# ─── 8. retry: increment attempt counter ─────────────────────────────

def retry_or_fallback_node(state: AgentState) -> dict:
    """Record retry attempt, increment counter, log failure."""
    current_attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)
    tool_results = state.get("tool_results", [])
    errors = state.get("errors", [])

    # Increment attempt (NOT in tool_node to avoid double-counting)
    new_attempt = current_attempt + 1

    # Build error message from latest failure
    latest_result = tool_results[-1] if tool_results else "unknown failure"
    error_msg = f"retry {new_attempt}/{max_attempts}: {latest_result[:100]}"

    return {
        "attempt": new_attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "completed", f"retry recorded: {new_attempt}/{max_attempts}")],
    }


# ─── 9. dead_letter: handle max retries exceeded ──────────────────────

def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries."""
    attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)
    errors = state.get("errors", [])

    final_answer = (
        f"This request could not be completed after {attempt} attempts "
        f"(max: {max_attempts}). The issue has been escalated to our support team. "
        f"Error reference: {errors[-1][:100] if errors else 'unknown'}"
    )

    return {
        "final_answer": final_answer,
        "events": [make_event("dead_letter", "completed", "max retries exceeded", attempts=attempt)],
    }


# ─── 10. finalize: emit final audit event ─────────────────────────────

def finalize_node(state: AgentState) -> dict:
    """Emit final audit event. All routes pass through here before END."""
    final_answer = state.get("final_answer", "")
    pending_question = state.get("pending_question", "")

    if final_answer:
        status = "completed with answer"
    elif pending_question:
        status = "completed with clarification"
    else:
        status = "completed (no answer)"

    return {
        "events": [make_event("finalize", "completed", "workflow finished", status=status)],
    }
