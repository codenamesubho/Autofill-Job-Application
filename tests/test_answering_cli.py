"""Failure modes for `autofill-answer` on bad inputs. No LLM call needed for
these — they must all be caught before any network I/O happens.
"""

import json

import pytest

from autofill_job_application.answering.cli import build_parser, run

pytestmark = pytest.mark.usefixtures("dummy_llm_env")


@pytest.fixture
def dummy_llm_env(monkeypatch):
    """Config must resolve before file checks run, so give it something valid."""
    monkeypatch.setenv("AUTOFILL_LLM_API_KEY", "k")
    monkeypatch.setenv("AUTOFILL_LLM_MODEL", "test/model")


@pytest.mark.asyncio
async def test_missing_snapshot_file_fails_cleanly(tmp_path):
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Alex Doe.")
    args = build_parser().parse_args(
        [str(tmp_path / "nope.json"), "--context", str(ctx)]
    )
    with pytest.raises(SystemExit, match="not found"):
        await run(args)


@pytest.mark.asyncio
async def test_missing_context_file_fails_cleanly(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text('{"jobs": []}')
    args = build_parser().parse_args(
        [str(snap), "--context", str(tmp_path / "nope.md")]
    )
    with pytest.raises(SystemExit, match="not found"):
        await run(args)


@pytest.mark.asyncio
async def test_snapshot_with_no_jobs_is_rejected_not_silently_empty(tmp_path):
    """RunResult defaults every field, so an unrelated JSON file would otherwise
    parse "successfully" into zero jobs and exit as if nothing was wrong."""
    snap = tmp_path / "wrong_shape.json"
    snap.write_text(json.dumps({"not": "a snapshot"}))
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Alex Doe.")
    args = build_parser().parse_args([str(snap), "--context", str(ctx)])
    with pytest.raises(SystemExit, match="no jobs"):
        await run(args)


@pytest.mark.asyncio
async def test_malformed_json_is_reported_not_a_traceback(tmp_path):
    snap = tmp_path / "broken.json"
    snap.write_text("{not valid json")
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Alex Doe.")
    args = build_parser().parse_args([str(snap), "--context", str(ctx)])
    with pytest.raises(SystemExit, match="Could not parse"):
        await run(args)


@pytest.mark.asyncio
async def test_empty_context_document_is_rejected(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"jobs": [{"input_url": "https://x.test", "questions": []}]}))
    ctx = tmp_path / "ctx.md"
    ctx.write_text("   \n  ")
    args = build_parser().parse_args([str(snap), "--context", str(ctx)])
    with pytest.raises(SystemExit, match="empty"):
        await run(args)


def test_context_flag_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["snap.json"])
