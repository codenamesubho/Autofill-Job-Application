from autofill.models import (
    Answer,
    AnswerSource,
    FieldType,
    ForwardControl,
    FormSchema,
    JobTarget,
    Locator,
    Option,
    Question,
    WidgetKind,
)


def make_question(**kw) -> Question:
    base = dict(
        field_id="f1",
        label="Email address",
        type=FieldType.EMAIL,
        locator={"strategy": "css", "value": "#email"},
    )
    base.update(kw)
    return Question(**base)


def test_question_round_trips_through_json():
    q = make_question(required=True, canonical_key="email")
    assert Question.model_validate_json(q.model_dump_json()) == q


def test_filled_ignores_whitespace_only_values():
    assert not make_question(current_value="   ").filled
    assert make_question(current_value="a@b.com").filled


def test_enum_types_are_flagged():
    assert FieldType.SELECT.is_enum
    assert FieldType.RADIO.is_enum
    assert not FieldType.TEXT.is_enum
    assert not FieldType.CONSENT.is_enum


def test_cache_key_is_label_normalized_and_option_sensitive():
    a = make_question(label="Email  address")
    b = make_question(label="  email ADDRESS  ")
    assert a.cache_key() == b.cache_key()

    with_opts = make_question(
        type=FieldType.SELECT,
        options=[Option(value="y", label="Yes"), Option(value="n", label="No")],
    )
    assert with_opts.cache_key() != a.cache_key()


def test_jd_dependent_cache_keys_are_scoped_per_job():
    """Guards against pasting one company's name into another's essay."""
    q = make_question(label="Why do you want this role?", type=FieldType.TEXTAREA)
    k1 = q.cache_key(jd_dependent=True, job_id="job-1")
    k2 = q.cache_key(jd_dependent=True, job_id="job-2")
    assert k1 != k2
    assert k1 != q.cache_key()


def test_answer_confidence_is_clamped():
    a = Answer(field_id="f1", value="x", source=AnswerSource.PROFILE, confidence=5.0)
    assert a.confidence == 1.0
    assert Answer(
        field_id="f1", value="x", source=AnswerSource.AGENT, confidence=-2
    ).confidence == 0.0


def test_blank_answer_detection():
    blank = Answer(field_id="f", value=None, source=AnswerSource.AGENT)
    assert blank.is_blank
    assert Answer(field_id="f", value="  ", source=AnswerSource.AGENT).is_blank
    assert not Answer(field_id="f", value="x", source=AnswerSource.PROFILE).is_blank


def test_unfilled_required_selects_only_open_required_fields():
    schema = FormSchema(
        url="https://example.com/apply",
        questions=[
            make_question(field_id="a", required=True),
            make_question(field_id="b", required=True, current_value="done"),
            make_question(field_id="c", required=False),
        ],
    )
    assert [q.field_id for q in schema.unfilled_required] == ["a"]


def test_job_target_id_is_stable_and_url_derived():
    url = "https://boards.example.com/jobs/9"
    assert JobTarget.make_id(url) == JobTarget.make_id(url)
    assert JobTarget.make_id(url) != JobTarget.make_id(url + "0")
