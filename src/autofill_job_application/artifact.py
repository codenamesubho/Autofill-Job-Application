"""Run output: a JSON artifact on disk plus a readable stdout summary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import JobSnapshot, RunResult


def write_run(result: RunResult, out_dir: str | Path) -> Path:
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{stamp}.json"
    path.write_text(result.model_dump_json(indent=2))
    return path


def summarize(snap: JobSnapshot) -> str:
    """One line per job, so a run can be checked without opening the JSON."""
    steps = snap.tier2.get("steps", "-")
    blocked = snap.guard.get("blocked", 0)
    flag = f"  ⚠ {blocked} submit attempt(s) blocked" if blocked else ""
    url = snap.input_url if len(snap.input_url) <= 58 else snap.input_url[:55] + "..."
    return (
        f"{url:<60} {snap.outcome:<24} "
        f"{len(snap.questions):>3} questions  {snap.required_count:>3} required  "
        f"steps={steps}{flag}"
    )


def print_report(result: RunResult, path: Path) -> None:
    print()
    for snap in result.jobs:
        print(summarize(snap))
        if snap.error:
            print(f"{'':<60} error: {snap.error}")
    print(f"\nArtifact: {path}")
