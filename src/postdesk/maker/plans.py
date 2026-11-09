"""
Templates: what a post looks like, as YAML (docs/templates.md). A template
is a sequence of typed slides; each says what the model writes for it
(`brief`), which fields it fills (`fields`), which are set from the facts
(`fixed`, with `{{ expressions }}`), whether it may be dropped
(`optional`), and when it applies (`if` / `unless`). `plan` turns a
template and a `Facts` into `Slot`s.

Expressions are small on purpose: a dotted path into the context
(`facts.hero`, `facts.subjects.0.name`, `account.site`, `month`, `n`), a
quoted literal, and `or` to fall back. A value that is one expression keeps
its type (an image dict stays a dict); text around one is interpolated.

Templates are looked up by name in `./templates/` (yours), on
`POSTDESK_TEMPLATES` (colon-separated), then in the package. A path to a
YAML file is used as is, which is how `postdesk make --plan` runs a plan
written for one post.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from postdesk.config import config
from postdesk.sources.base import Facts

SLIDE_TYPES = ("cover", "caption", "statement", "stat", "table", "label", "cta", "story")
KINDS = ("carousel", "image", "story")
PACKAGE_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
EXPR = re.compile(r"\{\{\s*(.*?)\s*\}\}")
MAX_REPEAT = 10


class TemplateError(ValueError):
    """A template that cannot be read, or that does not fit these facts."""


@dataclass
class Slot:
    type: str  # a renderer slide type
    brief: str  # what the model writes for it
    fields: tuple[str, ...]  # the copy fields the model fills; the first is the primary one
    fixed: dict = field(default_factory=dict)  # set from the facts
    optional: bool = False  # dropped when its primary field comes back empty

    @property
    def primary(self) -> str:
        return self.fields[0] if self.fields else ""


@dataclass
class Template:
    name: str
    kind: str
    slides: list[dict]
    story: dict | None
    description: str = ""
    kicker: str | None = None
    requires: list[dict] = field(default_factory=list)
    path: Path | None = None


def template_dirs() -> list[Path]:
    dirs = [Path("templates")]
    dirs += [Path(p) for p in os.environ.get("POSTDESK_TEMPLATES", "").split(":") if p]
    dirs.append(PACKAGE_TEMPLATES)
    return dirs


def available() -> dict[str, Path]:
    """name -> file, the first on the search path winning."""
    out: dict[str, Path] = {}
    for d in template_dirs():
        if d.is_dir():
            for p in sorted(d.glob("*.yaml")):
                out.setdefault(p.stem, p)
    return out


def load_template(name: str | Path | dict) -> Template:
    """By name, by path, or an already-parsed mapping (a `custom` plan)."""
    if isinstance(name, dict):
        return _template(name, None)
    p = Path(name)
    if p.suffix in (".yaml", ".yml") or p.is_file():
        if not p.is_file():
            raise TemplateError(f"template file {p} not found")
    else:
        found = available().get(str(name))
        if found is None:
            raise TemplateError(f"no template {name!r}; have {', '.join(sorted(available()))}")
        p = found
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TemplateError(f"{p}: not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise TemplateError(f"{p}: a template is a mapping with kind: and slides:")
    return _template(data, p)


def _template(data: dict, path: Path | None) -> Template:
    kind = data.get("kind", "carousel")
    if kind not in KINDS:
        raise TemplateError(f"kind must be one of {', '.join(KINDS)} ({kind!r} given)")
    slides = data.get("slides") or []
    if not isinstance(slides, list) or not slides:
        raise TemplateError("a template needs a list of slides")
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict) or s.get("type") not in SLIDE_TYPES:
            raise TemplateError(f"slide {i}: type must be one of {', '.join(SLIDE_TYPES)}")
        if kind == "story" and s["type"] != "story":
            raise TemplateError(f"slide {i}: a story-only template has only story frames")
        if kind != "story" and s["type"] == "story":
            raise TemplateError(f"slide {i}: a story frame goes under story:, not in the feed slides")
        if not isinstance(s.get("fields", []), list):
            raise TemplateError(f"slide {i}: fields is a list")
    story = data.get("story")
    if kind != "story" and (not isinstance(story, dict) or not story.get("fields")):
        raise TemplateError("a feed template needs a story: with the fields the model fills")
    return Template(
        name=str(data.get("name") or (path.stem if path else "custom")), kind=kind, slides=slides,
        story=story if kind != "story" else None, description=str(data.get("description") or ""),
        kicker=data.get("kicker"), requires=list(data.get("requires") or []), path=path,
    )


# ---------------------------------------------------------------- expressions


def _lookup(path: str, ctx: dict):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def evaluate(expr: str, ctx: dict):
    """`a.b or c or 'text'`: the first truthy value."""
    for alt in re.split(r"\s+or\s+", expr.strip()):
        alt = alt.strip()
        if not alt:
            continue
        if (alt[0] == alt[-1]) and alt[0] in "\"'" and len(alt) >= 2:
            value = alt[1:-1]
        else:
            value = _lookup(alt, ctx)
        if value not in (None, "", [], {}, False):
            return value
    return None


def render_value(value, ctx: dict):
    """A template value with its expressions filled in; nested mappings and
    lists are walked."""
    if isinstance(value, str):
        m = EXPR.fullmatch(value.strip())
        if m:
            return evaluate(m.group(1), ctx)
        return EXPR.sub(lambda mm: _text(evaluate(mm.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: render_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, ctx) for v in value]
    return value


def _text(v) -> str:
    return "" if v is None else str(v)


def context(facts: Facts, template: Template) -> dict:
    f = facts.as_dict()
    when = facts.published_at
    ctx = {
        "facts": f,
        "account": config.account.model_dump(),
        "month": when.strftime("%B %Y") if when else "",
        "date": when.strftime("%-d %b %Y") if when else "",
        "template": template.name,
    }
    ctx["kicker"] = render_value(template.kicker, ctx) if template.kicker else (f["extra"].get("kicker") or "New post")
    return ctx


# ---------------------------------------------------------------- the plan


def plan(template: Template, facts: Facts) -> tuple[str, list[Slot], Slot | None]:
    """(post kind, the feed slots, the Story slot) for these facts. For a
    story-only template the frames are the slots and there is no separate
    Story."""
    ctx = context(facts, template)
    for req in template.requires:
        if not evaluate(str(req.get("path", "")), ctx):
            raise TemplateError(req.get("message") or f"the {template.name} template needs {req.get('path')}")
    slots: list[Slot] = []
    for item in template.slides:
        if "if" in item and not evaluate(str(item["if"]), ctx):
            continue
        if "unless" in item and evaluate(str(item["unless"]), ctx):
            continue
        repeat = render_value(item.get("repeat", 1), ctx)
        try:
            repeat = int(repeat or 1)
        except (TypeError, ValueError):
            raise TemplateError(f"repeat must be a number ({repeat!r} given)") from None
        if not 1 <= repeat <= MAX_REPEAT:
            raise TemplateError(f"repeat is 1 to {MAX_REPEAT} ({repeat} given)")
        for n in range(1, repeat + 1):
            local = {**ctx, "n": n}
            fixed = render_value(item.get("fixed") or {}, local)
            fixed = {k: v for k, v in fixed.items() if v not in (None, "", [], {})}
            slots.append(Slot(
                type=item["type"], brief=_text(render_value(item.get("brief") or "", local)),
                fields=tuple(item.get("fields") or ()), fixed=fixed, optional=bool(item.get("optional")),
            ))
    if not slots:
        raise TemplateError(f"the {template.name} template has no slide that applies to these facts")
    story = None
    if template.story:
        fixed = render_value(template.story.get("fixed") or {}, ctx)
        story = Slot("story", _text(render_value(template.story.get("brief") or "", ctx)),
                     tuple(template.story.get("fields") or ("headline", "line")),
                     {k: v for k, v in fixed.items() if v not in (None, "", [], {})})
    return template.kind, slots, story
