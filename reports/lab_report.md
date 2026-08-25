# Lab Report

## Scenario Results

| Scenario | Thread ID | Expected route | Actual route | Success | Retries | History entries |
|---|---|---|---|---:|---:|---:|
| S01_simple | `thread-S01_simple` | simple | simple | yes | 0 | 6 |
| S02_tool | `thread-S02_tool` | tool | tool | yes | 0 | 8 |
| S03_missing | `thread-S03_missing` | missing_info | missing_info | yes | 0 | 6 |
| S04_risky | `thread-S04_risky` | risky | risky | yes | 0 | 10 |
| S05_error | `thread-S05_error` | error | error | yes | 2 | 12 |
| S06_delete | `thread-S06_delete` | risky | risky | yes | 0 | 10 |
| S07_dead_letter | `thread-S07_dead_letter` | error | error | yes | 1 | 7 |

## Persistence Evidence

- Checkpointer configured: `memory`.
- The compiled graph received this checkpointer and each invocation used
  `{"configurable": {"thread_id": state["thread_id"]}}`.
- Recorded checkpoint history entries: 59.
- Each row records the thread ID used for that scenario and the number of states
  returned by `graph.get_state_history()` for that same thread.

MemorySaver retains this evidence only for the lifetime of the process. SQLite
or Postgres is required before claiming cross-process crash recovery.
