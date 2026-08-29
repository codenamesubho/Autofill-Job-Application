import pytest

from autofill.models import AppState
from autofill.orchestrator.states import IllegalTransition, Status
from autofill.store import db
from autofill.store.repo import AnswerCacheRepo, ApplicationRepo, EventRepo


@pytest.fixture()
def conn(tmp_path):
    c = db.init_db(tmp_path / "state.db")
    yield c
    c.close()


def make_app(**kw) -> AppState:
    base = dict(id="job1", job_url="https://x.test/1", canonical_url="https://x.test/1")
    base.update(kw)
    return AppState(**base)


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "state.db"
    db.init_db(path).close()
    c = db.init_db(path)  # second call must not raise
    tables = {
        r["name"]
        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    c.close()
    assert {"applications", "questions", "answer_cache", "events"} <= tables


def test_upsert_dedupes_on_canonical_url(conn):
    repo = ApplicationRepo(conn)
    repo.upsert(make_app())
    repo.upsert(make_app(id="job2", company="Later"))
    assert len(repo.list()) == 1
    assert repo.get("job1") is not None


def test_status_counts(conn):
    repo = ApplicationRepo(conn)
    repo.upsert(make_app())
    repo.upsert(make_app(id="job2", canonical_url="https://x.test/2"))
    assert repo.counts_by_status() == {"PENDING": 2}


def test_legal_transition_is_persisted(conn):
    repo = ApplicationRepo(conn)
    repo.upsert(make_app())
    repo.set_status("job1", Status.OPENING)
    assert repo.get("job1").status == "OPENING"


def test_illegal_transition_is_refused(conn):
    repo = ApplicationRepo(conn)
    repo.upsert(make_app())
    with pytest.raises(IllegalTransition):
        repo.set_status("job1", Status.SUBMITTED_BY_HUMAN)


def test_agent_can_never_reach_submitted_on_its_own():
    """Only AWAITING_REVIEW leads to SUBMITTED_BY_HUMAN, and a human drives it."""
    from autofill.orchestrator.states import TRANSITIONS

    sources = [s for s, tos in TRANSITIONS.items() if Status.SUBMITTED_BY_HUMAN in tos]
    assert sources == [Status.AWAITING_REVIEW]


def test_answer_cache_put_get_and_use_count(conn):
    cache = AnswerCacheRepo(conn)
    assert cache.get("h1") is None
    cache.put("h1", "Email address", "email", "a@b.com")
    assert cache.get("h1") == "a@b.com"
    assert cache.size() == 1
    row = conn.execute(
        "SELECT uses FROM answer_cache WHERE question_hash='h1'"
    ).fetchone()
    assert row["uses"] == 1


def test_events_are_appended_in_order(conn):
    ApplicationRepo(conn).upsert(make_app())
    events = EventRepo(conn)
    events.log("job1", "opened", url="https://x.test/1")
    events.log("job1", "extracted", questions=12)
    kinds = [e["kind"] for e in events.for_application("job1")]
    assert kinds == ["opened", "extracted"]
