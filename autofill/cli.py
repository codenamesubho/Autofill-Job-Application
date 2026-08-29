"""Command line entry point.

Import discipline: nothing in this module's import graph may touch Playwright.
`autofill status` must run on a machine with no browser installed - the browser
packages are an optional extra, imported lazily inside the commands that need them.
"""

from __future__ import annotations

from pathlib import Path

import typer

from autofill.config import load_settings
from autofill.orchestrator.states import Status
from autofill.store import db
from autofill.store.repo import AnswerCacheRepo, ApplicationRepo
from autofill.store.site_memory import SiteMemoryRepo

app = typer.Typer(
    add_completion=False,
    help="Fill job applications automatically. Never submits them.",
)

MILESTONE_PENDING = "not implemented yet - milestone {m} (see PLAN.md section 7)"


def _display(path: Path, root: Path) -> str:
    """Repo-relative when it can be; absolute when settings point elsewhere."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _not_implemented(command: str, milestone: str) -> None:
    typer.secho(
        f"'{command}': " + MILESTONE_PENDING.format(m=milestone),
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Recreate starter files."),
) -> None:
    """Create the database, directories and starter data files."""
    s = load_settings()
    db.init_db(s.db_path)
    for d in (s.data_dir, s.config_dir, s.artifact_dir):
        d.mkdir(parents=True, exist_ok=True)

    starters: dict[Path, str] = {
        s.profile_file: _PROFILE_TEMPLATE,
        s.jobs_file: _JOBS_TEMPLATE,
    }
    for path, content in starters.items():
        if force or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            typer.echo(f"wrote {_display(path, s.root)}")

    typer.secho(f"initialized {s.db_path.name}", fg=typer.colors.GREEN)
    typer.echo("next: fill in data/profile.yaml and data/jobs.yaml")


@app.command()
def status() -> None:
    """Show queue counts and cache size."""
    s = load_settings()
    conn = db.init_db(s.db_path)
    try:
        apps = ApplicationRepo(conn)
        counts = apps.counts_by_status()
        cache_size = AnswerCacheRepo(conn).size()
        learned = SiteMemoryRepo(conn).size()
    finally:
        conn.close()

    total = sum(counts.values())
    typer.echo(f"db          {s.db_path}")
    typer.echo(f"review mode {s.review_mode}")
    typer.echo(f"cache       {cache_size} answers")
    typer.echo(f"site memory {learned} pages")
    typer.echo(f"queue       {total} applications")
    if not total:
        typer.echo("            (empty - run 'autofill init', then 'autofill ingest')")
        return
    for st in Status:
        n = counts.get(st.value, 0)
        if n:
            typer.echo(f"  {st.value:<20} {n}")


@app.command()
def ingest() -> None:
    """Load resume, experience doc, profile and job list."""
    _not_implemented("ingest", "M1")


@app.command()
def corpus() -> None:
    """Capture and replay offline form snapshots - the measuring instrument.

    M2 comes before the extractor on purpose: without a labelled offline corpus,
    "make the generic extractor better" is unfalsifiable.
    """
    _not_implemented("corpus", "M2")


@app.command()
def run() -> None:
    """Fill the queued applications. Stops before Submit, always."""
    _not_implemented("run", "M4")


@app.command()
def review() -> None:
    """Walk the filled applications awaiting your review."""
    _not_implemented("review", "M8")


@app.command()
def resume() -> None:
    """Re-fill interrupted applications from cached answers."""
    _not_implemented("resume", "M6")


_PROFILE_TEMPLATE = """\
# Deterministic facts. Everything here is used verbatim - never LLM-generated.
# Anything you leave blank is escalated to you rather than invented.
identity:
  full_name:
  first_name:
  last_name:
  email:
  phone:
location:
  city:
  state:
  country:
  willing_to_relocate:
links:
  linkedin:
  github:
  portfolio:
work_authorization:
  authorized_to_work:      # e.g. yes / no
  requires_sponsorship:    # e.g. yes / no
employment:
  notice_period:
  current_ctc:
  expected_ctc:
# EEO / demographic fields are left blank by default and handed to you.
# Set values here only if you want them filled automatically.
eeo: {}
"""

_JOBS_TEMPLATE = """\
# One entry per application. Only `url` is required.
jobs:
  - url: https://example.com/jobs/1234
    company: Example Corp
    title: Senior Engineer
"""


if __name__ == "__main__":
    app()
