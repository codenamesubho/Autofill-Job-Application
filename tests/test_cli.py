"""URL-file parsing and argument wiring. No browser, no key."""

import pytest

from autofill_job_application.artifact import summarize
from autofill_job_application.cli import build_parser, parse_url_file
from autofill_job_application.models import JobSnapshot, Outcome


def test_parses_plain_list():
    assert parse_url_file("https://a.test/1\nhttps://b.test/2\n") == [
        "https://a.test/1",
        "https://b.test/2",
    ]


def test_ignores_comments_and_blank_lines():
    text = """
    # my job list
    https://a.test/1

      https://b.test/2
    # https://c.test/3
    """
    assert parse_url_file(text) == ["https://a.test/1", "https://b.test/2"]


def test_strips_trailing_comment():
    assert parse_url_file("https://a.test/1  # acme, backend\n") == ["https://a.test/1"]


def test_empty_file_yields_nothing():
    assert parse_url_file("") == []
    assert parse_url_file("# only a comment\n\n") == []


def test_parser_defaults():
    args = build_parser().parse_args(["urls.txt"])
    assert args.urls_file == "urls.txt"
    assert args.out == "./snapshots"
    assert args.headful is False
    assert args.max_steps == 25
    assert args.job_timeout is None
    assert "chrome-profile" in args.profile_dir
    # model/provider come from the environment unless given explicitly
    assert args.model is None
    assert args.provider is None


def test_provider_choices_are_validated():
    args = build_parser().parse_args(["urls.txt", "--provider", "openai"])
    assert args.provider == "openai"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["urls.txt", "--provider", "hal9000"])


def test_parser_flags():
    args = build_parser().parse_args(
        ["urls.txt", "--headful", "--max-steps", "40", "--out", "/tmp/x", "--job-timeout", "120"]
    )
    assert args.headful is True
    assert args.max_steps == 40
    assert args.out == "/tmp/x"
    assert args.job_timeout == 120.0


def test_urls_file_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_summary_line_flags_blocked_submissions():
    """A blocked submit attempt must be visible in the run output, not buried."""
    snap = JobSnapshot(
        input_url="https://a.test/1",
        outcome=Outcome.FORM_OPEN,
        guard={"installed": True, "blocked": 2, "reasons": ["click on Submit"]},
    )
    line = summarize(snap)
    assert "form_open" in line
    assert "2 submit attempt(s) blocked" in line


def test_summary_line_is_quiet_when_nothing_blocked():
    snap = JobSnapshot(
        input_url="https://a.test/1",
        outcome=Outcome.FORM_OPEN,
        guard={"installed": True, "blocked": 0},
    )
    assert "blocked" not in summarize(snap)
