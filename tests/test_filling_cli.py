"""Failure modes for `autofill-fill` on bad inputs, mirroring
tests/test_answering_cli.py exactly: fail before any browser or network I/O.
"""

import pytest

from autofill_job_application.filling.cli import _validate_file_arg, build_parser, run

pytestmark = pytest.mark.usefixtures("dummy_llm_env")


@pytest.fixture
def dummy_llm_env(monkeypatch):
    monkeypatch.setenv("AUTOFILL_LLM_API_KEY", "k")
    monkeypatch.setenv("AUTOFILL_LLM_MODEL", "test/model")


def test_context_flag_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["urls.txt"])


def test_validate_file_arg_accepts_none():
    assert _validate_file_arg(None, "--resume") is None


def test_validate_file_arg_rejects_missing_path():
    with pytest.raises(SystemExit, match="does not exist"):
        _validate_file_arg("/nope/not-a-real-path.pdf", "--resume")


def test_validate_file_arg_rejects_a_directory(tmp_path):
    with pytest.raises(SystemExit, match="does not exist"):
        _validate_file_arg(str(tmp_path), "--resume")


def test_validate_file_arg_accepts_a_real_file(tmp_path):
    f = tmp_path / "resume.pdf"
    f.write_text("not a real pdf, just a fixture")
    assert _validate_file_arg(str(f), "--resume") == str(f)


@pytest.mark.asyncio
async def test_missing_urls_file_fails_cleanly(tmp_path):
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Alex Doe.")
    args = build_parser().parse_args(
        [str(tmp_path / "nope.txt"), "--context", str(ctx)]
    )
    with pytest.raises(SystemExit, match="not found"):
        await run(args)


@pytest.mark.asyncio
async def test_missing_context_file_fails_cleanly(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://ats.test/job\n")
    args = build_parser().parse_args(
        [str(urls), "--context", str(tmp_path / "nope.md")]
    )
    with pytest.raises(SystemExit, match="not found"):
        await run(args)


@pytest.mark.asyncio
async def test_missing_resume_path_fails_before_any_browser_work(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://ats.test/job\n")
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Alex Doe.")
    args = build_parser().parse_args(
        [str(urls), "--context", str(ctx), "--resume", str(tmp_path / "nope.pdf")]
    )
    with pytest.raises(SystemExit, match="does not exist"):
        await run(args)


@pytest.mark.asyncio
async def test_empty_urls_file_is_rejected(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("# only a comment\n\n")
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Alex Doe.")
    args = build_parser().parse_args([str(urls), "--context", str(ctx)])
    with pytest.raises(SystemExit, match="No URLs"):
        await run(args)
