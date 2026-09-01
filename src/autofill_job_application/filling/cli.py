"""`autofill-fill urls.txt --context about-me.md --resume resume.pdf` — fill
an application form's fields for human review. Never submits.

Takes job URLs directly, like autofill-snapshot, not a pre-made snapshot.json:
batches discover fields live as the form is filled (a "Next" click can reveal
a page of fields that didn't exist a moment ago), so a static snapshot from an
earlier run would already be stale by the time filling starts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..llm import PROVIDERS, LLMConfigError, build_llm, resolve_config
from .models import FillRun, WriteStatus
from .runner import DEFAULT_BATCH_TIMEOUT, DEFAULT_MAX_BATCHES, fill_job


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autofill-fill",
        description=(
            "Fill an already-open application form's fields from a context "
            "document, for human review. Never submits, and cannot even try: "
            "two independent layers make that structurally true."
        ),
    )
    p.add_argument("urls_file", help="file with one job URL per line")
    p.add_argument(
        "--context",
        required=True,
        help="document about you (markdown or text): experience, preferences, links",
    )
    p.add_argument(
        "--resume",
        default=None,
        help="local path to a resume file, for FILE-widget fields (never LLM-chosen)",
    )
    p.add_argument(
        "--cover-letter",
        default=None,
        help="local path to a cover letter file, used for fields whose label mentions 'cover'",
    )
    p.add_argument("--out", default="./fills", help="artifact directory")
    p.add_argument("--headful", action="store_true", help="show the browser window")
    p.add_argument("--max-steps", type=int, default=25, help="phase-1 (reach the form) step budget per job")
    p.add_argument("--max-batches", type=int, default=DEFAULT_MAX_BATCHES, help="fill-loop batch budget per job")
    p.add_argument("--job-timeout", type=float, default=None, help="wall-clock seconds before giving up on one job")
    p.add_argument("--batch-timeout", type=float, default=DEFAULT_BATCH_TIMEOUT, help="wall-clock seconds per residual-agent turn")
    p.add_argument("--model", default=None, help="defaults to $AUTOFILL_LLM_MODEL")
    p.add_argument("--provider", default=None, choices=sorted(PROVIDERS))
    p.add_argument(
        "--profile-dir",
        default="~/.autofill/chrome-profile",
        help="dedicated Chrome profile (never your real one)",
    )
    return p


def _validate_file_arg(path_str: str | None, flag: str) -> str | None:
    """Fail fast, before Chrome launches — same convention answering/cli.py
    already uses for its context-doc check."""
    if not path_str:
        return None
    path = Path(path_str).expanduser()
    if not path.exists() or not path.is_file():
        raise SystemExit(f"{flag} path does not exist or is not a file: {path}")
    return str(path)


def summarize(job) -> str:
    url = job.input_url if len(job.input_url) <= 58 else job.input_url[:55] + "..."
    return (
        f"{url:<60} {job.written_count:>3} filled  "
        f"{job.escalated_count:>3} for you  {job.failed_count:>3} failed"
    )


def print_report(run: FillRun, path: Path) -> None:
    print()
    for job in run.jobs:
        print(summarize(job))
        if job.error:
            print(f"{'':<60} error: {job.error}")
        for f in job.fields:
            if f.write_status is WriteStatus.ESCALATED:
                why = f.failure_reason or "not answered"
                print(f"    ↳ NEEDS YOU  {f.question_label[:44]:<44} {why[:60]}")
            elif f.write_status is WriteStatus.FAILED:
                print(f"    ↳ FAILED    {f.question_label[:44]:<44} {(f.failure_reason or '')[:60]}")
        blocked = job.guard.get("blocked", 0) if job.guard else 0
        if blocked:
            print(f"    ⚠ {blocked} submit attempt(s) blocked")
    print(f"\nFilled fields: {path}")
    print("Review every field before submitting. Nothing has been submitted.")
    print("Browser left open — close it yourself when done (it will block a "
          "later run against the same --profile-dir until you do).")


async def run(args) -> FillRun:
    from ..browser import start_session

    urls_path = Path(args.urls_file)
    if not urls_path.exists():
        raise SystemExit(f"URLs file not found: {urls_path}")
    urls = [
        line.split("#", 1)[0].strip()
        for line in urls_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    urls = [u for u in urls if u]
    if not urls:
        raise SystemExit(f"No URLs found in {args.urls_file}")

    context_path = Path(args.context)
    if not context_path.exists():
        raise SystemExit(f"Context document not found: {context_path}")
    context = context_path.read_text().strip()
    if not context:
        raise SystemExit(f"Context document {context_path} is empty")

    resume_path = _validate_file_arg(args.resume, "--resume")
    cover_letter_path = _validate_file_arg(args.cover_letter, "--cover-letter")

    config = resolve_config(model=args.model, provider=args.provider)
    llm = build_llm(config)
    print(f"model: {config.describe()}", file=sys.stderr)

    job_kwargs = {} if args.job_timeout is None else {"job_timeout": args.job_timeout}

    run_out = FillRun(
        context_path=str(context_path),
        resume_path=resume_path or "",
        cover_letter_path=cover_letter_path or "",
    )
    session = await start_session(headless=not args.headful, profile_dir=args.profile_dir)
    # The browser is deliberately left running when this returns, exactly
    # like autofill-snapshot — closing it is the user's call. It matters even
    # more here: the whole point of this tool is to fill fields for the user
    # to review and submit themselves, so auto-closing the browser the moment
    # this process exits would destroy the one place that review can happen.
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}", file=sys.stderr)
        run_out.jobs.append(
            await fill_job(
                session,
                url,
                context,
                llm=llm,
                config=config,
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                max_steps=args.max_steps,
                max_batches=args.max_batches,
                batch_timeout=args.batch_timeout,
                **job_kwargs,
            )
        )
    return run_out


def main() -> int:
    args = build_parser().parse_args()
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
    return 0 if any(j.written_count for j in run_out.jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
