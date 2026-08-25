# Day 08 Lab Report

## 1. Team / student

- Name: Nxust (Track3 — Day 23)
- Repo/commit: Track3-DAY23-Nxust
- Date: 2026

## 2. Architecture

### Graph nodes (11 total)

| Node | Responsibility |
|---|---|
| `intake` | Normalize raw query string (provided as working example) |
| `classify` | LLM + structured output → intent classification → sets `route` + `risk_level` |
| `answer` | LLM grounded response from tool_results + context |
| `clarify` | LLM generates a specific clarification question |
| `risky_action` | LLM describes the proposed action needing human approval |
| `approval` | Mock HITL (auto-approve); reads `proposed_action` → writes `approval` dict |
| `tool` | Mock tool; error simulation on `error` route for retry testing |
| `evaluate` | Retry gate — checks if tool result contains `"ERROR"` → sets `evaluation_result` |
| `retry` | Increment `attempt` counter, record failure in `errors` list |
| `dead_letter` | Escalate after max retries; write structured `final_answer` |
| `finalize` | Emit closing audit event; all paths converge here before END |

### Edges

- Fixed: `__start__` → `intake` → `classify`
- Fixed: `answer` → `finalize` → `__end__`
- Fixed: `clarify` → `finalize` → `__end__`
- Fixed: `tool` → `evaluate`
- Fixed: `risky_action` → `approval`
- Fixed: `dead_letter` → `finalize` → `__end__`
- Conditional (4 functions in `routing.py`):
  - `route_after_classify` → maps `route` string to next node
  - `route_after_evaluate` → `needs_retry` → `retry`, else → `answer`
  - `route_after_retry` → `attempt < max_attempts` → `tool`, else → `dead_letter`
  - `route_after_approval` → `approved` → `tool`, else → `clarify`

### State reducers

All node functions return a partial update dict. The graph applies it via LangGraph's default merge. No node mutates the input state directly.

## 3. State schema

| Field | Reducer | Why |
|---|---|---|
| `thread_id` | overwrite | Set once at init; never changes |
| `scenario_id` | overwrite | Set once at init |
| `query` | overwrite | Normalized in intake, then read-only |
| `route` | overwrite | Updated by classify; drives all conditional routing |
| `risk_level` | overwrite | Set by classify; used in risky_action_node |
| `attempt` | overwrite | Incremented by retry node only |
| `max_attempts` | overwrite | Set once from scenario config |
| `final_answer` | overwrite | Set by answer/clarify/dead_letter; final output |
| `evaluation_result` | overwrite | Retry gate; set by evaluate node |
| `pending_question` | overwrite | Clarification question; cleared when resolved |
| `proposed_action` | overwrite | Set by risky_action_node; consumed by approval_node |
| `approval` | overwrite | Dict matching ApprovalDecision schema; set by approval_node |
| `messages` | append (`add`) | Audit trail of intake events |
| `tool_results` | append (`add`) | Accumulated across retry iterations |
| `errors` | append (`add`) | Each retry appends its own failure entry |
| `events` | append (`add`) | Full audit log per node visit |

Critical design decision: `messages`, `tool_results`, `errors`, `events` all use `Annotated[..., add]` so each retry iteration appends rather than overwrites the history. All scalar fields (including `approval` dict and `attempt` counter) use default overwrite semantics — LangGraph's default merge handles this correctly.

## 4. Scenario results

From `outputs/metrics.json`:

| Scenario | Expected route | Actual route | Success | Retries | Interrupts |
|---|---|---|---:|---:|---:|
| S01_simple | simple | simple | yes | 0 | 0 |
| S02_tool | tool | tool | yes | 0 | 0 |
| S03_missing | missing_info | missing_info | yes | 0 | 0 |
| S04_risky | risky | risky | yes | 0 | 1 |
| S05_error | error | error | yes | 2 | 0 |
| S06_delete | risky | risky | yes | 0 | 1 |
| S07_dead_letter | error | error | yes | 1 | 0 |

**Summary:** 7/7 scenarios passed (100% success rate). Average nodes visited: 6.43. Total retries: 3 (all from error-route scenarios). Total interrupts (approval node visits): 2 (both risky scenarios).

## 5. Failure analysis

### 1. Retry or tool failure

The `error` route simulates transient failures. In `tool_node`, when `route == "error"` and `attempt < 2`, it returns `"ERROR: transient failure on attempt N"`. The `evaluate_node` detects the `"ERROR"` substring in `tool_results[-1]` and sets `evaluation_result = "needs_retry"`. The `route_after_evaluate` then routes back to `retry`, which increments `attempt`. On attempt 2, the condition `attempt < 2` fails and `tool_node` returns a success result instead.

S05_error has `max_attempts = 3` (default), so it survives two failures then succeeds on attempt 2. S07_dead_letter sets `max_attempts = 1`, so it exhausts retries on the first failure and immediately hits `dead_letter_node`.

Without the bounded retry check (`attempt < max_attempts` in `route_after_retry`), the error route would loop forever. The test confirms this is correctly implemented: S07 reaches `dead_letter` in exactly 5 nodes (intake → classify → retry → dead_letter → finalize).

### 2. Risky action without approval

The `risky` route goes through `risky_action` → `approval` → conditional. The `approval_node` is a mock that always returns `approved=True` unless `LANGGRAPH_INTERRUPT=true`. This means the approval path is wired correctly — the routing function `route_after_approval` checks `approval.get("approved")` and routes to `tool` on approve or `clarify` on reject.

In S04_risky and S06_delete, the approval node fires once each (`interrupt_count = 1`), then the graph proceeds through `tool` → `evaluate` → `answer` → `finalize`. The 8-node count for risky scenarios (vs 6 for tool) reflects the two extra nodes: `risky_action` and `approval`.

The failure mode here is that without a real interrupt, the approval is always granted. If a student enables `LANGGRAPH_INTERRUPT=true`, the graph pauses at the interrupt point, allowing manual review of `proposed_action` before resuming.

## 6. Persistence / recovery evidence

The compiled graph receives a `MemorySaver` checkpointer (configured in `persistence.py`, selected via `grading.yaml`/`lab.yaml`). Each scenario invocation uses `{"configurable": {"thread_id": state["thread_id"]}}` to isolate state per scenario.

Evidence from `metrics.json`:
- Total checkpoint history entries across all scenarios: **59**
- Per-scenario counts: S01=6, S02=8, S03=6, S04=10, S05=12, S06=10, S07=7

The checkpoint count scales with path length: `simple` (4 nodes) → 6 entries; `tool` (6 nodes) → 8 entries; `risky` (8 nodes) → 10 entries; `error` with 2 retries (10 nodes) → 12 entries. Each node visit creates one checkpoint, plus `__root__` and `__end__` entries.

`graph.get_state_history(config)` returns all checkpoints in order, enabling time-travel replay and crash-resume within a process. The `test_memory_checkpointer_keeps_history_per_thread` test verifies thread isolation — saving to `thread-persistence-a` does not create history for `thread-persistence-b`.

**Limitation:** `MemorySaver` stores state in process memory. The SQLite checkpointer extension (`persistence.py` raises `NotImplementedError` for `kind == "sqlite"`) is required for cross-process crash recovery. To productionize: implement `SqliteSaver` with WAL mode per the hint in `persistence.py`.

## 7. Extension work

No formal extension was implemented beyond the core requirements.

The lab guide lists several extension options:
- **SQLite persistence** — Not implemented; `persistence.py` raises `NotImplementedError` for `sqlite` kind
- **Real HITL with `interrupt()`** — Not enabled; `LANGGRAPH_INTERRUPT` env var not set
- **Time travel / state history replay** — Checkpointer supports this (via `get_state_history()`) but no explicit replay was demonstrated
- **Parallel fan-out with `Send()`** — Not implemented
- **Graph diagram via `graph.get_graph().draw_mermaid()`** — Not exported

To reach 90+ score, SQLite checkpointer implementation is the highest-value next step. It converts `MemorySaver` (ephemeral, in-memory) to `SqliteSaver` (durable, disk-backed), enabling cross-process resume and genuine crash recovery.

## 8. Improvement plan

If one more day, productionize in this order:

1. **SQLite checkpointer** — Implement `SqliteSaver` in `persistence.py`:
   - `pip install langgraph-checkpoint-sqlite`
   - `sqlite3.connect()` with WAL mode for concurrent reads
   - Replace `raise NotImplementedError` with actual `SqliteSaver` instantiation
   - Demonstrate crash recovery: kill process mid-run, resume from `get_state_history()[-2]`

2. **Real HITL** — Set `LANGGRAPH_INTERRUPT=true`, replace the mock approval dict with `interrupt(proposed_action)` in `approval_node`. This forces human review before any risky action proceeds.

3. **Graph diagram** — Call `graph.get_graph().draw_mermaid().split("\n")` and write to `outputs/graph.mmd`. Provides visual documentation of the routing logic.

4. **LLM-as-judge in `evaluate_node`** — Upgrade from substring detection (`"ERROR" in result`) to an LLM call that grades result quality. This earns the bonus points in the rubric and is more robust to varied error messages.

5. **Parallel fan-out with `Send()`** — For scenarios where `tool` could run two independent lookups concurrently (e.g., order status + customer profile), use `Send()` to fan out and aggregate results. Reduces latency for multi-tool scenarios.
