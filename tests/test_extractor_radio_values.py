"""collapse_radio_groups() must use the radio's real HTML value, not its
element_id or label, when building QuestionOption.value.

Regression test: a fill step matches an option by synthesizing a selector like
input[name="X"][value="Y"] from QuestionOption.value. If value is the label
text or a (usually absent) element_id instead of the real value attribute,
that selector will almost never match a real ATS radio input.
"""

from autofill_job_application.extract.js_extractor import collapse_radio_groups
from autofill_job_application.models import ElementRef, LabelSource, Question, WidgetType


def _radio(*, value_attr, label, checked, css_selector, element_id=None):
    return Question(
        ref=ElementRef(
            frame_url="https://ats.test/apply",
            css_selector=css_selector,
            element_id=element_id,
            name_attr="work_auth",
            value_attr=value_attr,
        ),
        label=label,
        label_source=LabelSource.WRAPPING_LABEL,
        widget=WidgetType.RADIO_GROUP,
        current_value="checked" if checked else None,
    )


def test_option_value_is_the_real_html_value_not_the_label():
    """The label ('Yes') and the real value ('1') commonly differ on real ATS
    forms; the fix must prefer the real value attribute."""
    members = [
        _radio(value_attr="1", label="Yes", checked=True, css_selector="#opt1"),
        _radio(value_attr="0", label="No", checked=False, css_selector="#opt2"),
    ]
    [merged] = collapse_radio_groups(members)
    values = {o.label: o.value for o in merged.options}
    assert values == {"Yes": "1", "No": "0"}


def test_option_value_falls_back_to_element_id_then_label_when_no_value_attr():
    """A radio with no value attribute (rare — browsers default value='on', so
    valueAttr would normally be 'on', but defend the fallback chain anyway)."""
    members = [
        _radio(value_attr=None, label="Remote", checked=False, css_selector="#opt1", element_id="remote-radio"),
        _radio(value_attr=None, label="Onsite", checked=False, css_selector="#opt2"),
    ]
    [merged] = collapse_radio_groups(members)
    values = {o.label: o.value for o in merged.options}
    assert values == {"Remote": "remote-radio", "Onsite": "Onsite"}


def test_option_selector_is_preserved_as_a_fallback():
    """Some ATS forms give every option in a group the same generic value
    ("on"), making name+value ambiguous. option_selector is the only way to
    disambiguate in that case, so it must survive the merge."""
    members = [
        _radio(value_attr="on", label="Yes", checked=True, css_selector="#opt1"),
        _radio(value_attr="on", label="No", checked=False, css_selector="#opt2"),
    ]
    [merged] = collapse_radio_groups(members)
    selectors = {o.label: o.option_selector for o in merged.options}
    assert selectors == {"Yes": "#opt1", "No": "#opt2"}
