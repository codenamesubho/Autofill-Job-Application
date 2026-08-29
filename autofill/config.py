"""Settings: paths, thresholds, budgets. Nothing here imports a browser.

Layering: defaults below < config/settings.yaml < AUTOFILL_* environment vars.
Thresholds start conservative on purpose (PLAN.md section 8.5) - trust is earned.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOFILL_", env_nested_delimiter="__", extra="ignore"
    )

    # --- paths -------------------------------------------------------------
    root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    config_dir: Path = PROJECT_ROOT / "config"
    artifact_dir: Path = PROJECT_ROOT / "artifacts"
    db_path: Path = PROJECT_ROOT / "state.db"
    browser_profile_dir: Path = PROJECT_ROOT / ".browser-profile"

    profile_file: Path = PROJECT_ROOT / "data" / "profile.yaml"
    experience_file: Path = PROJECT_ROOT / "data" / "candidate_experience.md"
    resume_file: Path = PROJECT_ROOT / "data" / "resume.pdf"
    jobs_file: Path = PROJECT_ROOT / "data" / "jobs.yaml"
    never_answer_file: Path = PROJECT_ROOT / "config" / "never_answer.yaml"

    # --- confidence routing (conservative defaults) ------------------------
    extract_confidence_threshold: float = 0.75  # below this, escalate to tier 2
    answer_confidence_threshold: float = 0.80   # below this, flag for human
    autofill_enum_threshold: float = 0.85       # constrained choices need more certainty

    # --- loop safety -------------------------------------------------------
    max_loop_iterations: int = 8
    per_job_timeout_seconds: int = 600
    per_job_token_budget: int = 60_000
    per_domain_min_delay_seconds: float = 2.0

    # --- model -------------------------------------------------------------
    # Left unset by default; M4 wires the Candidate Context Agent.
    model: str | None = None

    # --- behaviour ---------------------------------------------------------
    headless: bool = False       # attended mode wants to see the browser
    review_mode: str = "attended"  # attended | batch
    workers: int = 1             # serial by default; >1 only for the pre-pass

    extra: dict[str, Any] = Field(default_factory=dict)


def load_settings(path: Path | None = None) -> Settings:
    """Load defaults, overlay config/settings.yaml if present, then env vars.

    Init kwargs outrank environment variables in pydantic-settings, so a key the
    environment sets is dropped from the YAML overlay to keep AUTOFILL_* winning.
    """
    file = path or DEFAULT_SETTINGS_FILE
    overlay: dict[str, Any] = {}
    if file.exists():
        overlay = yaml.safe_load(file.read_text()) or {}
    overlay = {
        k: v for k, v in overlay.items() if f"AUTOFILL_{k.upper()}" not in os.environ
    }
    return Settings(**overlay)
