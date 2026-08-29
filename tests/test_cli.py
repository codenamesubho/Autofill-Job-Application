import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autofill.cli import app

runner = CliRunner()


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point every writable path at tmp so tests never touch the repo."""
    monkeypatch.setenv("AUTOFILL_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("AUTOFILL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOFILL_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTOFILL_PROFILE_FILE", str(tmp_path / "data" / "profile.yaml"))
    monkeypatch.setenv("AUTOFILL_JOBS_FILE", str(tmp_path / "data" / "jobs.yaml"))
    return tmp_path


def test_status_exits_zero_on_a_fresh_database(isolated):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "0 applications" in result.output


def test_init_creates_db_and_starter_files(isolated):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (isolated / "state.db").exists()
    profile = yaml.safe_load((isolated / "data" / "profile.yaml").read_text())
    assert "identity" in profile and "work_authorization" in profile


def test_init_does_not_clobber_an_edited_profile(isolated):
    runner.invoke(app, ["init"])
    profile = isolated / "data" / "profile.yaml"
    profile.write_text("identity:\n  email: me@example.com\n")
    runner.invoke(app, ["init"])
    assert "me@example.com" in profile.read_text()


@pytest.mark.parametrize("command", ["ingest", "corpus", "run", "review", "resume"])
def test_unimplemented_commands_exit_nonzero(isolated, command):
    result = runner.invoke(app, [command])
    assert result.exit_code == 2
    assert "not implemented" in result.output


def test_status_does_not_import_playwright(tmp_path):
    """The M0 gate: `autofill status` must run with no browser installed."""
    code = (
        "import sys; from typer.testing import CliRunner; from autofill.cli import app; "
        "r = CliRunner().invoke(app, ['status']); "
        "sys.exit(1 if 'playwright' in sys.modules else r.exit_code)"
    )
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "AUTOFILL_DB_PATH": str(tmp_path / "state.db")},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_never_answer_config_is_loadable_and_covers_the_hard_cases():
    root = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((root / "config" / "never_answer.yaml").read_text())
    patterns = " ".join(cfg["label_patterns"]).lower()
    for must in ("salary", "sponsorship", "criminal", "i certify"):
        assert must in patterns
    assert "consent" in cfg["field_types"]
    assert cfg["eeo"]["autofill_from_profile"] is False
