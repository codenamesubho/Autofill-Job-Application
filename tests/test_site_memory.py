import pytest

from autofill.store import db
from autofill.store.site_memory import SiteMemory, SiteMemoryRepo, domain_of


@pytest.fixture()
def repo(tmp_path):
    conn = db.init_db(tmp_path / "state.db")
    yield SiteMemoryRepo(conn)
    conn.close()


def make_mem(**kw) -> SiteMemory:
    base = dict(
        domain="jobs.example.test",
        page_fingerprint="fp1",
        apply_entry_selector="button.apply",
        extraction_tier_used=1,
        widget_strategies={"f1": "aria-combobox"},
    )
    base.update(kw)
    return SiteMemory(**base)


def test_domain_of_strips_scheme_and_www():
    assert domain_of("https://www.Jobs.Example.test/careers/1") == "jobs.example.test"
    assert domain_of("not a url") == ""


def test_remember_and_get_round_trip(repo):
    assert repo.get("jobs.example.test", "fp1") is None
    repo.remember(make_mem())
    got = repo.get("jobs.example.test", "fp1")
    assert got.apply_entry_selector == "button.apply"
    assert got.widget_strategies == {"f1": "aria-combobox"}


def test_remember_is_idempotent_per_domain_and_fingerprint(repo):
    repo.remember(make_mem())
    repo.remember(make_mem(apply_entry_selector="a.apply-now"))
    assert repo.size() == 1
    assert repo.get("jobs.example.test", "fp1").apply_entry_selector == "a.apply-now"


def test_a_different_page_on_the_same_domain_is_a_separate_record(repo):
    repo.remember(make_mem())
    repo.remember(make_mem(page_fingerprint="fp2"))
    assert repo.size() == 2


def test_memory_is_trusted_only_while_it_keeps_working(repo):
    repo.remember(make_mem())
    repo.record_success("jobs.example.test", "fp1")
    assert repo.get("jobs.example.test", "fp1").is_trusted

    repo.record_failure("jobs.example.test", "fp1")
    assert not repo.get("jobs.example.test", "fp1").is_trusted


def test_fresh_memory_is_not_trusted_before_it_has_succeeded():
    assert not make_mem().is_trusted


def test_forget_removes_the_record(repo):
    repo.remember(make_mem())
    repo.forget("jobs.example.test", "fp1")
    assert repo.get("jobs.example.test", "fp1") is None
