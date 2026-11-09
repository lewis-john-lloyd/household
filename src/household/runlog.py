"""What a stage prints, kept line by line for the views' Status page (run_log and run_notes in Reports)."""

import json

import pandas as pd

from . import logs, schema, sheets


def say(log, message, level=None):
    """Print a line through log, with its level (logs.Line; INFO unless given or already set)."""
    if log:
        log(logs.Line(message, level or logs.level_of(message)))


def recording(log):
    """(say, lines): say prints through log as before and keeps every line, for the views' status page."""
    lines = []

    def say(message):
        line = message if isinstance(message, logs.Line) else logs.Line(str(message))
        lines.append(line)
        if log:
            log(line)

    return say, lines


STAGES = {"ingest", "compute", "run"}  # run: runner.run's own lines and its table of stages


def record_stage(reports, stage, run_id, at, summary, lines):
    """Keep this run of this stage in Reports (run_log: its summary as JSON; run_notes: every line it
    printed), replacing that stage's last run and leaving the other stages' alone."""
    for name, rows in (
        (
            "run_log",
            [
                {
                    "run_id": run_id,
                    "computed_at": at,
                    "stage": stage,
                    "detail": json.dumps(summary, default=str, sort_keys=True),
                }
            ],
        ),
        (
            "run_notes",
            [
                {"run_id": run_id, "stage": stage, "at": at, "n": n, "message": str(m), "level": logs.level_of(m)}
                for n, m in enumerate(lines)
            ],
        ),
    ):
        t = schema.table(schema.REPORTS, name)
        kept = sheets.read_table(reports, t)
        kept = kept[kept["stage"].astype(str).isin(STAGES - {stage})] if len(kept) else kept
        sheets.replace_table(reports, t, pd.concat([kept, pd.DataFrame(rows, columns=t.columns)], ignore_index=True))
