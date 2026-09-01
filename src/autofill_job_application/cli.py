"""`autofill-snapshot urls.txt` — catalogue the questions on each job's form."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .artifact import print_report, write_run
from .llm import PROVIDERS, LLMConfigError
from .models import RunResult


def parse_url_file(text: str) -> list[str]:
    """One URL per line. `#` comments and blank lines ignored."""
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line.split("#", 1)[0].strip() if "#" in line else line)
    return [u for u in urls if u]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autofill-snapshot",
        description="Navigate to job applications and snapshot their questions. Never submits.",
    )
    p.add_argument("urls_file", help="file with one job URL per line")
    p.add_argument("--out", default="./snapshots", help="artifact directory")
    p.add_argument("--headful", action="store_true", help="show the browser window")
    p.add_argument("--max-steps", type=int, default=25, help="agent steps per job")
    p.add_argument(
        "--job-timeout",
        type=float,
        default=None,
        help=(
            "wall-clock seconds before giving up on one job and moving to the "
            "next (default: agent_runner.DEFAULT_JOB_TIMEOUT)"
        ),
    )
    p.add_argument(
        "--model",
        default=None,
        help="model id; defaults to $AUTOFILL_LLM_MODEL (e.g. anthropic/claude-opus-5)",
    )
    p.add_argument(
        "--provider",
        default=None,
        choices=sorted(PROVIDERS),
        help="routing backend; defaults to $AUTOFILL_LLM_PROVIDER or openrouter",
    )
    p.add_argument(
        "--profile-dir",
        default="~/.autofill/chrome-profile",
        help="dedicated Chrome profile (never your real one)",
    )
    return p


async def run(args) -> RunResult:
    # Imported here so `--help` and argument errors work without a browser or key.
    from .agent_runner import snapshot_one
    from .browser import start_session
    from .llm import build_llm, resolve_config

    urls = parse_url_file(Path(args.urls_file).read_text())
    if not urls:
        raise SystemExit(f"No URLs found in {args.urls_file}")

    # Resolve and construct before launching Chrome, so a config mistake costs
    # nothing and reports immediately.
    config = resolve_config(model=args.model, provider=args.provider)
    llm = build_llm(config)
    print(f"model: {config.describe()}", file=sys.stderr)

    # Explicit only when the user overrides it, so snapshot_one keeps using its
    # own default otherwise.
    job_kwargs = {} if args.job_timeout is None else {"job_timeout": args.job_timeout}

    result = RunResult()
    session = await start_session(
        headless=not args.headful, profile_dir=args.profile_dir
    )
    # The browser is deliberately left running when this returns — closing it
    # is the user's call, not this tool's. Nothing here ever calls
    # session.kill(); see browser.py's keep_alive comment for why an
    # individual Agent.run() call can't kill it out from under this loop
    # either.
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}", file=sys.stderr)
        result.jobs.append(
            await snapshot_one(
                session, url, llm=llm, max_steps=args.max_steps, config=config, **job_kwargs
            )
        )
    return result


def load_env() -> None:
    """Load a local .env so the documented key placement actually works.

    python-dotenv arrives with browser-use; if it is somehow absent, an exported
    environment variable still works and we carry on silently.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            return


def main() -> int:
    args = build_parser().parse_args()
    load_env()
    try:
        result = asyncio.run(run(args))
    except LLMConfigError as exc:  # missing key/model, or an unknown provider
        print(f"error: {exc}", file=sys.stderr)
        return 2
    path = write_run(result, args.out)
    print_report(result, path)
    # A partial batch still produced a usable artifact; only a total wipeout fails.
    return 0 if any(j.questions for j in result.jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
