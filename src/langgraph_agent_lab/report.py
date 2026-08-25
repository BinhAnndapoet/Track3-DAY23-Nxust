"""Report generation helper.

TODO(student): implement report rendering using MetricsReport data
and the template in reports/lab_report_template.md.
"""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport


def render_report(metrics: MetricsReport) -> str:
    """Render metrics plus verifiable checkpointer evidence as Markdown."""
    rows = "\n".join(
        "| {scenario_id} | `{thread_id}` | {expected} | {actual} | {success} | {retries} | {history} |".format(
            scenario_id=item.scenario_id,
            thread_id=item.thread_id,
            expected=item.expected_route,
            actual=item.actual_route or "-",
            success="yes" if item.success else "no",
            retries=item.retry_count,
            history=item.checkpoint_history_entries,
        )
        for item in metrics.scenario_metrics
    )
    return f"""# Lab Report

## Scenario Results

| Scenario | Thread ID | Expected route | Actual route | Success | Retries | History entries |
|---|---|---|---|---:|---:|---:|
{rows}

## Persistence Evidence

- Checkpointer configured: `{metrics.checkpointer_kind}`.
- The compiled graph received this checkpointer and each invocation used
  `{{\"configurable\": {{\"thread_id\": state[\"thread_id\"]}}}}`.
- Recorded checkpoint history entries: {metrics.total_checkpoint_history_entries}.
- Each row records the thread ID used for that scenario and the number of states
  returned by `graph.get_state_history()` for that same thread.

MemorySaver retains this evidence only for the lifetime of the process. SQLite
or Postgres is required before claiming cross-process crash recovery.
"""


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    """Write the rendered report to a file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
