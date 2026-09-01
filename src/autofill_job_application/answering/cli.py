"""`autofill-answer snapshot.json --context you.md` — draft answers for review.

Reads a snapshot produced by `autofill-snapshot` plus a document about the
candidate, and writes draft answers. It opens no browser and fills no form.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..llm import LLMConfigError, PROVIDERS, build_llm, resolve_config
from ..models import RunResult
from .models import AnswerRun, AnswerSource, JobAnswers
from .resolver import answer_job


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autofill-answer",
        description=(
            "Draft answers to a snapshot's questions from a context document. "
            "Writes a file for you to review; never fills or submits anything."
        ),
    )
    p.add_argument("snapshot", help="a snapshots/<timestamp>.json file")
    p.add_argument(
        "--context",
        required=True,
        help="document about you (markdown or text): experience, preferences, links",
    )
    p.add_argument("--out", default="./answers", help="artifact directory")
    p.add_argument("--model", default=None, help="defaults to $AUTOFILL_LLM_MODEL")
    p.add_argument("--provider", default=None, choices=sorted(PROVIDERS))
    return p


def summarize(job: JobAnswers) -> str:
    url = job.input_url if len(job.input_url) <= 58 else job.input_url[:55] + "..."
    return (
        f"{url:<60} {job.answered_count:>3} drafted  "
        f"{job.escalated_count:>3} for you"
    )


def print_report(run: AnswerRun, path: Path) -> None:
    print()
    for job in run.jobs:
        print(summarize(job))
        if job.error:
            print(f"{'':<60} error: {job.error}")
        # Escalations first — they are the ones needing a human.
        for a in job.answers:
            if a.source is AnswerSource.ESCALATED:
                why = a.escalation_reason or "not answered"
                print(f"    ↳ NEEDS YOU  {a.question_label[:44]:<44} {why[:60]}")
    print(f"\nDraft answers: {path}")
    print("Review and edit before using them. Nothing has been entered anywhere.")


async def run(args) -> AnswerRun:
    # Config first: a bad key or model should be reported before we even try to
    # read the input files, so the failure mode is consistent with autofill-snapshot.
    config = resolve_config(model=args.model, provider=args.provider)
    llm = build_llm(config)

    snapshot_path = Path(args.snapshot)
    context_path = Path(args.context)
    if not snapshot_path.exists():
        raise SystemExit(f"Snapshot file not found: {snapshot_path}")
    if not context_path.exists():
        raise SystemExit(f"Context document not found: {context_path}")

    try:
        result = RunResult.model_validate_json(snapshot_path.read_text())
    except Exception as exc:
        raise SystemExit(f"Could not parse {snapshot_path} as a snapshot artifact: {exc}")
    if not result.jobs:
        # Every field on RunResult defaults, so an unrelated JSON file parses
        # "successfully" into zero jobs. That is never what the user meant.
        raise SystemExit(
            f"{snapshot_path} has no jobs in it. "
            "Is this a snapshots/<timestamp>.json file from autofill-snapshot?"
        )

    context = context_path.read_text().strip()
    if not context:
        raise SystemExit(f"Context document {context_path} is empty")

    print(f"model: {config.describe()}", file=sys.stderr)

    run_out = AnswerRun(
        snapshot_path=str(snapshot_path), context_path=str(context_path)
    )
    for i, job in enumerate(result.jobs, 1):
        print(f"[{i}/{len(result.jobs)}] {job.input_url}", file=sys.stderr)
        run_out.jobs.append(await answer_job(job, context, llm=llm, config=config))
    return run_out


def main() -> int:
    args = build_parser().parse_args()
    # Reuse the snapshot CLI's .env loading so both tools read the same file.
    from ..cli import load_env

    load_env()
    try:
        run_out = asyncio.run(run(args))
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{stamp}.json"
    path.write_text(run_out.model_dump_json(indent=2))
    print_report(run_out, path)
    return 0 if any(j.answered_count for j in run_out.jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
