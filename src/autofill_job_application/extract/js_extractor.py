"""PARKED — not used by v1. Nothing imports this module.

v1 runs a single path: the browser-use Agent (`agent_runner.py`) both navigates
and reports the questions. This deterministic extractor is kept because it works
and was validated against real pages — on a live GitLab/Greenhouse posting it
returned all 28 questions with correct labels, widgets and required flags, and on
a cross-origin embedded form it returned all 7 where top-frame JS saw none.

Bring it back if agent accuracy or cost disappoints; it needs no API key and is
deterministic. Known rough edges to fix first, observed on the real Greenhouse run:
custom-combobox internals leak as duplicate `Select...` rows, invisible footer
elements are reported, and generic labels ("Attach") are not resolved against a
nearby heading.

---

Tier 1: deterministic form extraction over CDP. No LLM, no tokens.

Two traversals are needed and neither subsumes the other:

* **In-page (JS).** Walks the document, open shadow roots, and *same-origin*
  iframes via `contentDocument`.
* **Cross-origin (Python).** A cross-origin iframe is a separate CDP target whose
  DOM is unreachable from the parent's JS — measured, not assumed: a top-frame
  `Runtime.evaluate` returned 0 fields for an embedded form, and `contentDocument`
  raised a TypeError. Those frames are attached individually and given their own
  `Runtime.evaluate`.
"""

from __future__ import annotations

import json
import re

from ..models import (
    ElementRef,
    LabelSource,
    Question,
    QuestionOption,
    SourceTier,
    WidgetType,
)

#: Third-party iframes that are never part of an application form. Without this,
#: every Greenhouse snapshot gains a phantom question from the reCAPTCHA frame.
IGNORED_FRAME_HOSTS = re.compile(
    r"(recaptcha|google\.com/recaptcha|gstatic\.com|googletagmanager|google-analytics|"
    r"doubleclick|hotjar|segment\.(io|com)|intercom|drift\.com|youtube\.com|"
    r"facebook\.com|linkedin\.com/px|cookiebot|onetrust)",
    re.I,
)

EXTRACT_JS = r"""
(() => {
  const OUT = [];

  const visible = (el) => {
    const s = el.ownerDocument.defaultView.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return !(r.width === 0 && r.height === 0);
  };

  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();

  function cssPath(el) {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      const tag = cur.tagName.toLowerCase();
      const parent = cur.parentNode;
      if (!parent || parent.nodeType !== 1) { parts.unshift(tag); break; }
      const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
      parts.unshift(sibs.length > 1 ? `${tag}:nth-of-type(${sibs.indexOf(cur) + 1})` : tag);
      cur = parent;
    }
    return parts.join(' > ');
  }

  function domPath(el) {
    const idx = [];
    let cur = el;
    while (cur && cur.parentNode && cur.nodeType === 1) {
      idx.unshift(Array.prototype.indexOf.call(cur.parentNode.children, cur));
      cur = cur.parentNode;
    }
    return idx.join('.');
  }

  function precedingText(el) {
    // Nearest text-bearing element above this one inside the same layout block.
    let node = el;
    for (let up = 0; up < 4 && node; up++) {
      let sib = node.previousElementSibling;
      while (sib) {
        if (!/^(script|style|input|select|textarea)$/i.test(sib.tagName)) {
          const t = clean(sib.innerText || sib.textContent);
          if (t && t.length < 200) return t;
        }
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return '';
  }

  function deriveLabel(el, doc) {
    if (el.labels && el.labels.length) {
      const t = clean(el.labels[0].innerText);
      if (t) return {text: t, src: 'for-attr'};
    }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = clean(lb.split(/\s+/).map(id => {
        const n = doc.getElementById(id);
        return n ? n.innerText : '';
      }).join(' '));
      if (t) return {text: t, src: 'aria-labelledby'};
    }
    const al = el.getAttribute('aria-label');
    if (al && clean(al)) return {text: clean(al), src: 'aria-label'};
    const wrap = el.closest('label');
    if (wrap) {
      const t = clean(wrap.innerText);
      if (t) return {text: t, src: 'wrapping-label'};
    }
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg && clean(lg.innerText)) return {text: clean(lg.innerText), src: 'legend'};
    }
    const pt = precedingText(el);
    if (pt) return {text: pt, src: 'preceding-text'};
    if (el.placeholder && clean(el.placeholder)) return {text: clean(el.placeholder), src: 'placeholder'};
    if (el.name) return {text: el.name.replace(/[_\-\.]+/g, ' ').trim(), src: 'name-attr'};
    return {text: '', src: 'none'};
  }

  function widgetOf(el) {
    const role = (el.getAttribute('role') || '').toLowerCase();
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') return el.multiple ? 'select_native' : 'select_native';
    if (tag === 'textarea') return 'textarea';
    if (role === 'combobox' || role === 'listbox') return 'select_aria';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'radio') return 'radio_group';
      if (t === 'checkbox') return 'checkbox';
      if (['email','tel','number','date','url','file'].includes(t)) return t;
      return 'text';
    }
    return 'unknown';
  }

  function optionsOf(el, doc) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      return Array.from(el.options).map(o => ({
        value: o.value, label: clean(o.text), selected: o.selected,
      }));
    }
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'combobox' || role === 'listbox') {
      // Read options WITHOUT clicking: many widgets pre-render a hidden listbox
      // and point at it with aria-controls/aria-owns.
      const id = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
      const owned = id ? doc.getElementById(id) : null;
      const src = owned || el;
      const opts = Array.from(src.querySelectorAll('[role="option"]'));
      if (opts.length) {
        return opts.map(o => ({
          value: o.getAttribute('data-value') || clean(o.innerText),
          label: clean(o.innerText),
          selected: o.getAttribute('aria-selected') === 'true',
        }));
      }
      return null;   // unknowable without interaction; caller must not read this as "no options"
    }
    return [];
  }

  function requiredOf(el) {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    if (el.labels && el.labels.length && /\*/.test(el.labels[0].innerText)) return true;
    const wrap = el.closest('label');
    if (wrap && /\*/.test(wrap.innerText)) return true;
    return false;
  }

  const SEL = 'input, select, textarea, [role="combobox"], [role="listbox"]';
  const SKIP_TYPES = new Set(['hidden', 'submit', 'button', 'image', 'reset']);

  function walk(root, doc) {
    let nodes = [];
    try { nodes = Array.from(root.querySelectorAll(SEL)); } catch (e) { return; }
    for (const el of nodes) {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (tag === 'input' && SKIP_TYPES.has(type)) continue;   // never treat a submit control as a question
      if (tag === 'button') continue;
      let vis = true;
      try { vis = visible(el); } catch (e) {}
      const lab = deriveLabel(el, doc);
      OUT.push({
        cssSelector: cssPath(el),
        domPath: domPath(el),
        elementId: el.id || null,
        nameAttr: el.getAttribute('name') || null,
        label: lab.text,
        labelSource: lab.src,
        widget: widgetOf(el),
        required: requiredOf(el),
        options: optionsOf(el, doc),
        placeholder: el.placeholder || null,
        currentValue: (type === 'checkbox' || type === 'radio')
          ? (el.checked ? 'checked' : null)
          : (el.value || null),
        // Raw HTML value attribute, independent of checked state. This is what
        // a fill step must match against to select a specific radio option —
        // currentValue on a radio means "is it checked", never "what value".
        valueAttr: (el.value != null && el.value !== '') ? el.value : null,
        visible: vis,
      });
    }
    // same-origin iframes
    try {
      for (const f of Array.from(root.querySelectorAll('iframe'))) {
        try { if (f.contentDocument) walk(f.contentDocument, f.contentDocument); } catch (e) {}
      }
    } catch (e) {}
    // open shadow roots
    try {
      for (const el of Array.from(root.querySelectorAll('*'))) {
        if (el.shadowRoot) walk(el.shadowRoot, doc);
      }
    } catch (e) {}
  }

  walk(document, document);
  return JSON.stringify(OUT);
})()
"""

COUNT_JS = (
    "(() => document.querySelectorAll("
    "'input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea'"
    ").length)()"
)


def _confidence(raw: dict) -> float:
    """How much to trust this row. Decays with weaker label evidence."""
    score = 1.0
    src = raw.get("labelSource")
    if src == "none":
        score -= 0.6
    elif src in ("name-attr", "placeholder"):
        score -= 0.25
    elif src == "preceding-text":
        score -= 0.2
    if raw.get("options", 0) is None:
        score -= 0.2  # ARIA widget whose choices we could not read
    if not raw.get("visible", True):
        score -= 0.15
    return max(0.0, min(1.0, round(score, 3)))


def _to_question(raw: dict, frame_url: str, target_id: str | None) -> Question:
    opts = raw.get("options")
    notes = []
    if opts is None:
        notes.append("ARIA widget: options not enumerable without clicking")
    if not raw.get("visible", True):
        notes.append("not visible at extraction time")

    try:
        label_source = LabelSource(raw.get("labelSource") or "none")
    except ValueError:
        label_source = LabelSource.NONE
    try:
        widget = WidgetType(raw.get("widget") or "unknown")
    except ValueError:
        widget = WidgetType.UNKNOWN

    return Question(
        ref=ElementRef(
            frame_url=frame_url,
            target_id=target_id,
            css_selector=raw.get("cssSelector"),
            dom_path=raw.get("domPath"),
            element_id=raw.get("elementId"),
            name_attr=raw.get("nameAttr"),
            value_attr=raw.get("valueAttr"),
        ),
        label=raw.get("label") or "",
        label_source=label_source,
        widget=widget,
        required=bool(raw.get("required")),
        options=None if opts is None else [QuestionOption(**o) for o in opts],
        placeholder=raw.get("placeholder"),
        current_value=raw.get("currentValue"),
        confidence=_confidence(raw),
        source=SourceTier.TIER1,
        notes="; ".join(notes) or None,
    )


def collapse_radio_groups(questions: list[Question]) -> list[Question]:
    """A radio group is one question, not one per button.

    Radios sharing a `name` within a frame are folded into a single question whose
    options are the individual buttons' labels.
    """
    out: list[Question] = []
    groups: dict[tuple[str, str], list[Question]] = {}
    for q in questions:
        if q.widget is WidgetType.RADIO_GROUP and q.ref.name_attr:
            groups.setdefault((q.ref.frame_url, q.ref.name_attr), []).append(q)
        else:
            out.append(q)

    for (frame_url, name), members in groups.items():
        first = members[0]
        # The group's question text is the shared context (a legend or fieldset
        # label), so prefer a label common to all members over a per-button one.
        shared = [m.label for m in members if m.label_source in (LabelSource.LEGEND, LabelSource.ARIA_LABELLEDBY)]
        label = shared[0] if shared else (first.label or name)
        out.append(
            Question(
                ref=first.ref,
                label=label,
                label_source=first.label_source,
                widget=WidgetType.RADIO_GROUP,
                required=any(m.required for m in members),
                options=[
                    QuestionOption(
                        # Real HTML value first — this is what a selector like
                        # input[name=X][value=Y] must match. element_id/label
                        # are only a last resort for a radio with no value
                        # attribute at all (rare; browsers default value="on").
                        value=m.ref.value_attr or m.ref.element_id or m.label,
                        label=m.label,
                        selected=m.current_value == "checked",
                        # Some ATS forms give every option in a group the same
                        # generic value ("on"), making name+value ambiguous —
                        # this per-option selector is the fallback for that case.
                        option_selector=m.ref.css_selector,
                    )
                    for m in members
                ],
                current_value=next(
                    (m.label for m in members if m.current_value == "checked"), None
                ),
                confidence=min(m.confidence for m in members),
                source=SourceTier.TIER1,
                notes=first.notes,
            )
        )
    return out


def parse_rows(payload: str, frame_url: str, target_id: str | None = None) -> list[Question]:
    """Turn one frame's raw JS payload into Questions."""
    if not payload:
        return []
    rows = json.loads(payload) if isinstance(payload, str) else payload
    return [_to_question(r, frame_url, target_id) for r in rows]


def is_ignorable_frame(url: str) -> bool:
    """Third-party frames (captcha, analytics) are not part of the form."""
    return bool(url) and bool(IGNORED_FRAME_HOSTS.search(url))


async def _evaluate(cdp, expression: str, session_id):
    res = await cdp.cdp_client.send.Runtime.evaluate(
        params={"expression": expression, "returnByValue": True, "awaitPromise": True},
        session_id=session_id,
    )
    return res.get("result", {}).get("value")


async def count_fields(session) -> int:
    """Cheap 'is a form open here' probe across the top frame and real iframes."""
    cdp = await session.get_or_create_cdp_session()
    total = int(await _evaluate(cdp, COUNT_JS, cdp.session_id) or 0)
    for target_id, url in await _iframe_targets(session, cdp):
        try:
            sid = await _attach(cdp, target_id)
            total += int(await _evaluate(cdp, COUNT_JS, sid) or 0)
        except Exception:
            continue
    return total


async def _iframe_targets(session, cdp) -> list[tuple[str, str]]:
    targets = await cdp.cdp_client.send.Target.getTargets()
    out = []
    for t in targets.get("targetInfos", []):
        if t.get("type") != "iframe":
            continue
        url = t.get("url", "")
        if is_ignorable_frame(url):
            continue
        out.append((t["targetId"], url))
    return out


async def _attach(cdp, target_id: str) -> str:
    att = await cdp.cdp_client.send.Target.attachToTarget(
        params={"targetId": target_id, "flatten": True}
    )
    sid = att["sessionId"]
    await cdp.cdp_client.send.Runtime.enable(session_id=sid)
    return sid


async def extract_tier1(session) -> list[Question]:
    """Full deterministic extraction: top frame + same-origin + cross-origin frames."""
    cdp = await session.get_or_create_cdp_session()
    top_url = await session.get_current_page_url()

    questions = parse_rows(await _evaluate(cdp, EXTRACT_JS, cdp.session_id), top_url, None)

    for target_id, url in await _iframe_targets(session, cdp):
        try:
            sid = await _attach(cdp, target_id)
            payload = await _evaluate(cdp, EXTRACT_JS, sid)
            questions += parse_rows(payload, url, target_id)
        except Exception:
            continue  # a frame we cannot read is a gap, never a crash

    return collapse_radio_groups(questions)
