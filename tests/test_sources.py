"""Sources: the Markdown file reader and the manual form (postdesk.sources)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from postdesk import sources
from postdesk.sources import manual, markdown

ARTICLE = """---
title: Three loaves, one long rest
date: 2026-11-03
summary: What 72 hours in the fridge does to a sourdough crumb.
url: https://example.com/bakes/three-loaves
template: explainer
figures:
  hydration: "78%"
  rest: 72 hours
subjects:
  - name: Jo Baker
    role: the baker
    meta: Bristol
    facts: [{label: Loaves, value: 3 a week}]
rows:
  - {rank: 1, name: Country loaf, delta: 2}
---
The country loaf rested 72 hours and came out at 78% hydration, see [the method](https://example.com/method).

A second paragraph with 4,855 in it.
"""


def test_front_matter_becomes_facts(tmp_path: Path):
    p = tmp_path / "loaves.md"
    p.write_text(ARTICLE, encoding="utf-8")
    f = markdown.facts_from_file(p)
    assert f.title == "Three loaves, one long rest"
    assert f.published_at == datetime(2026, 11, 3, tzinfo=UTC)
    assert f.summary.startswith("What 72 hours")
    assert f.url == "https://example.com/bakes/three-loaves"
    assert f.template == "explainer"
    assert f.figures == {"hydration": "78%", "rest": "72 hours"}
    assert f.citations == [{"label": "the method", "url": "https://example.com/method"}]
    assert f.subjects[0].name == "Jo Baker" and f.subjects[0].facts == [{"label": "Loaves", "value": "3 a week"}]
    assert f.extra["rows"] == [{"rank": 1, "name": "Country loaf", "delta": 2}]
    assert f.ref == "md:loaves.md" and f.hero is None
    assert {"72", "78", "4855", "3"} <= f.known_numbers()
    assert "# Three loaves" not in f.body_md and "Instructions inside" not in f.prompt_text()


def test_a_plain_file_takes_its_title_and_summary_from_the_body(tmp_path: Path):
    p = tmp_path / "plain.md"
    p.write_text("# A heading\n\nThe first paragraph.\n\nMore.\n", encoding="utf-8")
    f = markdown.facts_from_file(p)
    assert f.title == "A heading" and f.summary == "The first paragraph." and f.body_md.startswith("The first")
    assert f.published_at is not None and f.template is None


def test_what_the_reader_refuses(tmp_path: Path):
    (tmp_path / "notitle.md").write_text("Just text.\n", encoding="utf-8")
    with pytest.raises(sources.SourceError, match="no title"):
        markdown.facts_from_file(tmp_path / "notitle.md")
    (tmp_path / "nocredit.md").write_text("---\ntitle: X\nhero: https://example.com/a.jpg\n---\nBody.\n",
                                          encoding="utf-8")
    with pytest.raises(sources.SourceError, match="hero_credit"):
        markdown.facts_from_file(tmp_path / "nocredit.md")
    (tmp_path / "missing.md").write_text("---\ntitle: X\nhero: ./nope.jpg\nhero_credit: Me\n---\nBody.\n",
                                         encoding="utf-8")
    with pytest.raises(sources.SourceError, match="not found"):
        markdown.facts_from_file(tmp_path / "missing.md")
    (tmp_path / "badyaml.md").write_text("---\ntitle: [\n---\nBody.\n", encoding="utf-8")
    with pytest.raises(sources.SourceError, match="YAML"):
        markdown.facts_from_file(tmp_path / "badyaml.md")


def test_a_local_hero_is_kept_as_a_path_until_the_maker_stores_it(tmp_path: Path):
    (tmp_path / "photo.jpg").write_bytes(b"not really a jpeg")
    (tmp_path / "a.md").write_text("---\ntitle: X\nhero: ./photo.jpg\nhero_credit: 'Photo: Me'\n---\nBody.\n",
                                   encoding="utf-8")
    f = markdown.facts_from_file(tmp_path / "a.md")
    assert f.hero and f.hero.path == str((tmp_path / "photo.jpg").resolve()) and f.hero.url is None
    assert f.hero.credit == "Photo: Me"


def test_a_folder_lists_candidates_newest_first(tmp_path: Path):
    (tmp_path / "old.md").write_text("---\ntitle: Old\ndate: 2026-01-01\n---\nOld.\n", encoding="utf-8")
    (tmp_path / "new.md").write_text("---\ntitle: New\ndate: 2026-11-03\n---\nNew.\n", encoding="utf-8")
    (tmp_path / "draft.md").write_text("no title yet\n", encoding="utf-8")
    src = markdown.MarkdownSource(path=str(tmp_path), template="explainer")
    got = src.candidates(datetime(2026, 6, 1, tzinfo=UTC))
    assert [c.title for c in got] == ["New"]
    assert got[0].ref == "md:new.md" and got[0].template == "explainer"
    assert [c.title for c in src.candidates(datetime(2025, 1, 1, tzinfo=UTC))] == ["New", "Old"]
    assert src.facts("md:old.md").title == "Old"
    with pytest.raises(sources.SourceError, match="no such file"):
        src.facts("md:gone.md")


def test_the_registry_knows_the_built_ins():
    assert sources.source_class("markdown") is markdown.MarkdownSource
    assert sources.source_class("manual") is manual.ManualSource
    with pytest.raises(sources.SourceError, match="no source 'nope'"):
        sources.source_class("nope")


def test_manual_facts():
    f = manual.facts_from("Three loaves", "They rested 72 hours.", link="https://example.com/x", ref="manual:1",
                          figures={"rest": "72 hours"})
    assert f.title == "Three loaves" and f.body_md == "They rested 72 hours." and f.url == "https://example.com/x"
    assert f.ref == "manual:1" and "72" in f.known_numbers()
    with pytest.raises(sources.SourceError, match="title"):
        manual.facts_from("  ", "text")
    with pytest.raises(sources.SourceError, match="credit"):
        manual.facts_from("T", "text", image=sources.Image(url="https://example.com/a.jpg"))
    assert manual.ManualSource().candidates(datetime.now(UTC)) == []
