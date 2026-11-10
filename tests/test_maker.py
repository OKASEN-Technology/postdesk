"""
The maker (postdesk.maker): templates to slots, the copy checks, and the
run end to end against the test database with Claude faked (`call=`) and
the renderer faked with httpx.MockTransport. The fake copy has one bad
figure on its first try, so the corrective turn is exercised every time.
"""

import io
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from postdesk import maker, queue
from postdesk.config import config, settings
from postdesk.db.models import AgentRun, ContentAsset, SocialPost
from postdesk.main import create_app
from postdesk.sources.base import Facts, Subject
from postdesk.sources.base import Image as SourceImage
from postdesk.sources.markdown import facts_from_file

NOW = datetime(2031, 3, 3, 10, 0, tzinfo=UTC)
make_mod = sys.modules["postdesk.maker.run"]
RealRenderer = maker.Renderer  # tests below patch the name the code looks up
RENDERER_SCHEMA = Path(__file__).resolve().parent.parent / "renderer" / "schema" / "slide.json"


def _facts(**extra) -> Facts:
    return Facts(
        title="Three loaves, one long rest",
        summary="What 72 hours in the fridge does to a sourdough crumb.",
        body_md="The country loaf rested 72 hours and came out at 78% hydration. Three loaves, 4,855 grams of flour.",
        url="https://example.com/bakes/three-loaves",
        published_at=NOW,
        figures={"hydration": "78%"},
        ref="md:loaves.md",
        **extra,
    )


def _copy_for(slots: int, figure="78%", caption=None) -> dict:
    words = {
        "kicker": "Weekend bake", "kicker_sub": "", "headline": "Three loaves, one long rest",
        "headline_em": "long rest", "sub": "What 72 hours in the fridge does.", "body": "The crumb opened up.",
        "source": "Source: the bake notes", "figure": figure, "figure_sub": "Water to flour, by weight",
        "note": "Wetter than most.", "line1": "Bakes three loaves most weekends.", "line2": "",
        "line": "The full method is at the link in bio.", "text": "Open, glossy and uneven.",
    }
    return {
        "slides": [{"slot": i, **words} for i in range(1, slots + 1)],
        "caption": caption if caption is not None else
        "Three loaves went in the fridge for 72 hours.\n\nThe full method is at the link in bio.\n\n"
        "#homebaking #sourdough",
        "alt_texts": [f"Slide {i}: words on a dark green ground." for i in range(1, slots + 1)],
        "story": {"headline": "Three loaves, one long rest", "line": "The method is at the link in bio.",
                  "alt_text": "A Story frame with the headline."},
    }


def _fake_call(first_figure="9,999"):
    """Scripted Claude: a bad figure on the first reply, a good one after."""
    calls = []

    def call(*, system, messages, schema):
        calls.append({"system": system, "messages": messages, "schema": schema})
        n = len(re.findall(r"^Slot \d+ \(", messages[0]["content"], re.M))
        return _copy_for(n, first_figure if len(calls) == 1 else "78%")

    call.calls = calls
    return call


def _jpeg(size=(1080, 1350), seed: str = "") -> bytes:
    """A JPEG whose bytes depend on `seed`, since storage is content-addressed."""
    buf = io.BytesIO()
    h = sum(map(ord, seed))
    Image.new("RGB", size, (20 + h % 200, 49, 43)).save(buf, format="JPEG")
    return buf.getvalue()


def _renderer(rendered: list | None = None, *, fail: int | None = None):
    def handler(request: httpx.Request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True, "version": "0.1.0", "themes": ["default"]})
        assert request.url.path == "/render" and request.headers["authorization"] == "Bearer t"
        spec = json.loads(request.content)
        if rendered is not None:
            rendered.append(spec)
        if fail is not None:
            return httpx.Response(fail, json={"detail": "nope"})
        size = (1080, 1920) if spec["type"] == "story" else (1080, 1350)
        return httpx.Response(200, content=_jpeg(size, json.dumps(spec, sort_keys=True)),
                              headers={"content-type": "image/jpeg"})

    return RealRenderer("http://renderer.test", "t", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def _voice(monkeypatch):
    monkeypatch.setattr(config.voice, "hashtags", ["#homebaking", "#sourdough", "#weekendbake"])
    monkeypatch.setattr(config.voice, "max_hashtags", 3)


# ---------------------------------------------------------------- plans


def test_the_package_schema_is_the_renderers():
    ours = json.loads(maker.render.SCHEMA_PATH.read_text(encoding="utf-8"))
    assert ours == json.loads(RENDERER_SCHEMA.read_text(encoding="utf-8")), \
        "copy renderer/schema/slide.json to src/postdesk/maker/slide.schema.json"


def test_every_built_in_template_plans_the_sequence_it_promises():
    f = _facts(subjects=[Subject(name="Jo Baker", role="the baker", meta="Bristol",
                                 facts=[{"label": "Loaves", "value": "3 a week"}])],
               extra={"rows": [{"rank": 1, "name": "Country loaf", "delta": 2}], "frames": 2})
    want = {
        "article": ("carousel", ["cover", "statement", "statement", "statement", "stat", "cta"]),
        "explainer": ("carousel", ["cover", "statement", "statement", "statement", "cta"]),
        "spotlight": ("carousel", ["label", "statement", "stat", "label", "cta"]),
        "stat": ("image", ["stat"]),
        "list": ("carousel", ["cover", "table", "cta"]),
        "story-only": ("story", ["story", "story"]),
    }
    for name, (kind, types) in want.items():
        got_kind, slots, story = maker.plan(maker.load_template(name), f)
        assert (got_kind, [s.type for s in slots]) == (kind, types), name
        assert (story is None) == (kind == "story"), name
    _, slots, _ = maker.plan(maker.load_template("article"), f)
    assert slots[0].fields == ("headline", "sub", "figure") and slots[0].fixed["kicker"] == "New post"
    assert slots[0].fixed["kicker_sub"] == "March 2031" and slots[0].fixed["link"] == config.account.site
    assert slots[4].optional and slots[4].primary == "figure"
    assert [s.fixed["kicker"] for s in maker.plan(maker.load_template("explainer"), f)[1][1:4]] == \
        ["What happened", "Why it matters", "What to watch"]
    _, spot, story = maker.plan(maker.load_template("spotlight"), f)
    assert spot[0].fixed["name"] == "Jo Baker" and spot[3].fixed["facts"] == [{"label": "Loaves", "value": "3 a week"}]
    _, rows, _ = maker.plan(maker.load_template("list"), f)
    assert rows[1].fixed["rows"] == f.extra["rows"] and rows[1].fixed["footer_right"] == config.account.site


def test_a_blank_site_leaves_the_link_off_rather_than_empty(monkeypatch):
    # Before `postdesk init` the site is "". An empty fixed field is dropped,
    # so the renderer gets no link at all instead of a blank one.
    monkeypatch.setattr(config.account, "site", "")
    _, slots, story = maker.plan(maker.load_template("article"), _facts())
    assert "link" not in slots[0].fixed and "kicker" in slots[0].fixed
    assert story is not None and "link" not in story.fixed


def test_a_hero_changes_the_cover_and_the_story():
    f = _facts(hero=SourceImage(url="https://example.com/crumb.jpg", credit="Photo: Jo Baker"))
    _, slots, _story = maker.plan(maker.load_template("article"), f)
    assert slots[0].fields == ("headline", "sub")
    assert slots[0].fixed["image"] == {"url": "https://example.com/crumb.jpg", "credit": "Photo: Jo Baker",
                                       "fit": "cover"}


def test_templates_say_what_they_need_and_refuse_what_they_cannot_render(tmp_path: Path):
    with pytest.raises(maker.TemplateError, match="needs a subject"):
        maker.plan(maker.load_template("spotlight"), _facts())
    with pytest.raises(maker.TemplateError, match="needs its rows"):
        maker.plan(maker.load_template("list"), _facts())
    with pytest.raises(maker.TemplateError, match="no template 'nope'"):
        maker.load_template("nope")
    bad = tmp_path / "bad.yaml"
    bad.write_text("kind: carousel\nslides:\n  - type: poster\n", encoding="utf-8")
    with pytest.raises(maker.TemplateError, match="type must be one of"):
        maker.load_template(bad)
    bad.write_text("kind: carousel\nslides:\n  - {type: cover, fields: [headline, sub]}\n", encoding="utf-8")
    with pytest.raises(maker.TemplateError, match="needs a story"):
        maker.load_template(bad)
    # A user's folder wins over the package's.
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "article.yaml").write_text(
        "name: article\nkind: image\nslides:\n  - {type: stat, brief: b, fields: [figure, figure_sub, kicker]}\n"
        "story: {brief: s, fields: [headline, line]}\n", encoding="utf-8")
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert maker.load_template("article").kind == "image"
    finally:
        os.chdir(old)


# ---------------------------------------------------------------- copy checks


def test_copy_checks_catch_invented_numbers_em_dashes_length_and_the_caption():
    f = _facts()
    kind, slots, story = maker.plan(maker.load_template("article"), f)
    known = maker.known_numbers(f)
    assert maker.check_copy(_copy_for(len(slots)), slots, known) == []

    bad = maker.check_copy(_copy_for(len(slots), "9,999"), slots, known)
    assert any("does not appear" in p and "9,999" in p for p in bad)
    assert maker.check_copy(_copy_for(len(slots), "+78.4%"), slots, known) == []  # rounds a known value

    dashed = _copy_for(len(slots))
    dashed["caption"] = "A strong bake — and early.\n\n#homebaking"
    assert any("em dash" in p for p in maker.check_copy(dashed, slots, known))

    long = _copy_for(len(slots))
    long["slides"][1]["body"] = "x" * 300
    problems = maker.check_copy(long, slots, known)
    assert any(p.startswith("slot 2: body 'xxx") and "300 characters" in p for p in problems)

    tagged = _copy_for(len(slots))
    tagged["caption"] += " #bread #flour"
    assert any("4 hashtags" in p for p in maker.check_copy(tagged, slots, known))
    retired = _copy_for(len(slots))
    retired["caption"] = "This bake is a game-changer.\n\n#homebaking"
    assert any("game-changer" in p for p in maker.check_copy(retired, slots, known))

    short = _copy_for(len(slots))
    short["alt_texts"] = short["alt_texts"][:-1]
    assert any("alt texts" in p for p in maker.check_copy(short, slots, known))
    missing = _copy_for(len(slots))
    missing["slides"].pop()
    assert f"slot {len(slots)}: missing" in maker.check_copy(missing, slots, known)

    # An optional stat with no figure is fine; build_slides drops it and numbers the statements.
    empty = _copy_for(len(slots), figure="")
    assert maker.check_copy(empty, slots, known) == []
    specs, alts, st = maker.build_slides(f, kind, slots, story, empty)
    assert [s["type"] for s in specs] == ["cover", "statement", "statement", "statement", "cta"]
    assert [s["n"] for s in specs] == [1, 2, 3, 4, 5] and all(s["total"] == 5 for s in specs)
    assert [s.get("num") for s in specs[1:4]] == ["01", "02", "03"]
    assert specs[1]["source"] == "Source: the bake notes" and specs[0]["kicker_sub"] == "March 2031"
    assert specs[4]["headline"] == "Three loaves, one" and specs[4]["headline_em"] == "long rest"
    assert len(alts) == 5 and st["type"] == "story" and st["kicker"] == "New post" and st["line"].endswith("bio.")
    for s in [*specs, st]:
        assert maker.validate_spec(s) == [], s


def test_a_story_only_post_needs_no_caption():
    f = _facts(extra={"frames": 2})
    kind, slots, story = maker.plan(maker.load_template("story-only"), f)
    copy = _copy_for(2, caption="")
    assert maker.check_copy(copy, slots, maker.known_numbers(f), caption=False, story=False) == []
    specs, _, st = maker.build_slides(f, kind, slots, story, copy)
    assert st is None and [s["type"] for s in specs] == ["story", "story"] and "n" not in specs[0]


def test_write_copy_sends_the_problems_back_and_gives_up_after_three():
    f = _facts()
    kind, slots, story = maker.plan(maker.load_template("article"), f)
    call = _fake_call()
    out = maker.write_copy(f, kind, slots, story, call=call, guidance="Lead with the crumb.")
    assert out["slides"][4]["figure"] == "78%" and len(call.calls) == 2
    first = call.calls[0]["messages"][0]["content"]
    assert "Lead with the crumb." in first and "Slot 1 (cover)" in first and "78%" in first
    assert call.calls[0]["schema"] == maker.words.COPY_SCHEMA
    assert "does not appear" in call.calls[1]["messages"][-1]["content"]
    assert "#homebaking #sourdough #weekendbake" in call.calls[0]["system"] and "never more than 3" in \
        call.calls[0]["system"].lower().replace("never more than 3", "never more than 3")

    with pytest.raises(maker.MakerError, match="3 times"):
        maker.write_copy(f, kind, slots, story, call=lambda **_: _copy_for(len(slots), "9,999"))


def test_the_voice_file_is_read_whole(tmp_path: Path, monkeypatch):
    (tmp_path / "voice.md").write_text("# Voice\n\nDry, specific, kind.\n", encoding="utf-8")
    monkeypatch.setattr(config, "path", tmp_path / "postdesk.toml")
    monkeypatch.setattr(config.voice, "file", "voice.md")
    assert "Dry, specific, kind." in maker.system_prompt()
    monkeypatch.setattr(config.voice, "file", "missing.md")
    assert "No voice file was found" in maker.system_prompt()


def test_patch_spec_keeps_to_the_slides_words_and_the_known_numbers():
    spec = {"type": "statement", "n": 2, "total": 5, "kicker": "Why it works", "headline": "Cold dough is",
            "headline_em": "patient dough.", "body": "A 72-hour rest.", "source": "Source: notes"}
    known = {"72"}
    out = maker.patch_spec(spec, {"headline": "Cold dough waits", "headline_em": ""}, known)
    assert out["headline"] == "Cold dough waits" and "headline_em" not in out and out["n"] == 2
    with pytest.raises(maker.MakerError, match="editable words are"):
        maker.patch_spec(spec, {"image": "x"}, known)
    with pytest.raises(maker.MakerError, match="not in the post's sources"):
        maker.patch_spec(spec, {"body": "A 96-hour rest."}, known)
    with pytest.raises(maker.MakerError, match="em dash"):
        maker.patch_spec(spec, {"body": "Rest — then bake."}, known)
    with pytest.raises(maker.MakerError, match="is empty"):
        maker.patch_spec(spec, {"headline": " "}, known)
    assert maker.slide_words(spec) == {"kicker": "Why it works", "headline": "Cold dough is",
                                       "headline_em": "patient dough.", "body": "A 72-hour rest.",
                                       "source": "Source: notes"}
    assert maker.slide_words(None) is None


def test_the_renderer_client_validates_before_it_posts_and_reports_failures(monkeypatch):
    monkeypatch.setattr(maker.render.time, "sleep", lambda _: None)
    rendered = []
    r = _renderer(rendered)
    assert r.health()["ok"] is True
    with pytest.raises(maker.RenderError, match="spec is invalid: spec: 'headline' is a required property"):
        r.render({"type": "cover", "kicker": "k", "sub": "s"})
    with pytest.raises(maker.RenderError, match="type: must be one of"):
        r.render({"type": "poster"})
    assert rendered == []
    out = r.render({"type": "cta", "kicker": "k", "headline": "Read the", "headline_em": "method.", "body": "b"})
    assert out[:2] == b"\xff\xd8" and rendered[0]["type"] == "cta"
    with pytest.raises(maker.RenderError, match=r"failed \(500\): nope"):
        _renderer(fail=500).render({"type": "cta", "kicker": "k", "headline": "h", "body": "b"})
    with pytest.raises(maker.RenderError, match="RENDERER_URL"):
        maker.Renderer("", None).render({"type": "cta", "kicker": "k", "headline": "h", "body": "b"})
    bad = maker.Renderer("http://x.test", "t", client=httpx.Client(transport=httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)))))
    with pytest.raises(maker.RenderError, match="ConnectError"):
        bad.render({"type": "cta", "kicker": "k", "headline": "h", "body": "b"})


def test_renderer_retries_temporary_gateway_failures_without_exposing_html(monkeypatch):
    delays = []
    monkeypatch.setattr(maker.render.time, "sleep", delays.append)
    responses = iter([httpx.Response(502, text="<html>gateway down</html>"),
                      httpx.Response(503, text="<html>starting</html>"),
                      httpx.Response(200, content=b"jpeg", headers={"content-type": "image/jpeg"})])
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={}) if req.method == "GET" else next(responses)))
    spec = {"type": "cta", "kicker": "k", "headline": "h", "body": "b"}
    assert maker.Renderer("http://renderer.test", client=client).render(spec) == b"jpeg"
    assert delays == [3, 8]

    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(502, text="<html>secret</html>")))
    with pytest.raises(maker.RenderError, match="temporarily unavailable") as error:
        maker.Renderer("http://renderer.test", client=client).render(spec)
    assert "<html>" not in str(error.value)


def test_renderer_wakes_sleeping_service_before_retry(monkeypatch):
    monkeypatch.setattr(maker.render.time, "sleep", lambda _: None)
    methods = []

    def handler(request):
        methods.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={})
        if len(methods) == 1:
            return httpx.Response(502)
        return httpx.Response(200, content=b"jpeg", headers={"content-type": "image/jpeg"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    spec = {"type": "cta", "kicker": "k", "headline": "h", "body": "b"}
    assert maker.Renderer("http://renderer.test", client=client).render(spec) == b"jpeg"
    assert methods == [("POST", "/render"), ("GET", "/health"), ("POST", "/render")]


def test_renderer_does_not_retry_invalid_requests(monkeypatch):
    delays = []
    monkeypatch.setattr(maker.render.time, "sleep", delays.append)
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"detail": "bad slide"})))
    with pytest.raises(maker.RenderError, match="bad slide"):
        maker.Renderer("http://renderer.test", client=client).render(
            {"type": "cta", "kicker": "k", "headline": "h", "body": "b"})
    assert delays == []


# ---------------------------------------------------------------- end to end


def test_make_files_a_carousel_and_its_story_as_drafts(session, tmp_path: Path):
    f = _facts()
    rendered, call = [], _fake_call()
    posts = maker.make(session, f, "article", call=call, renderer=_renderer(rendered), now=NOW)
    assert [p.kind for p in posts] == ["carousel", "story"] and {p.status for p in posts} == {"draft"}
    post, story = posts
    assert post.source_ref == story.source_ref == "md:loaves.md" and post.created_by == "maker"
    assert [s["spec"]["type"] for s in post.slides] == ["cover", "statement", "statement", "statement", "stat", "cta"]
    assert [s["type"] for s in rendered] == [*(s["spec"]["type"] for s in post.slides), "story"]
    assert post.caption.startswith("Three loaves") and post.publish_at > NOW and story.publish_at > post.publish_at
    assert story.slides[0]["spec"]["headline"] == "Three loaves, one long rest"
    assert all(s["alt_text"] for s in post.slides)
    asset = session.get(ContentAsset, __import__("uuid").UUID(post.slides[0]["asset_id"]))
    assert asset.content_type == "image/jpeg" and asset.uploaded_by == "maker"
    assert asset.provenance["source"] == "render" and asset.provenance["generated"] is False
    assert asset.provenance["template"] == "article" and asset.provenance["theme"] == config.theme.name
    # The bad first figure cost a corrective turn; a run is not logged here because the call was faked.
    assert len(call.calls) == 2
    assert session.execute(select(AgentRun)).scalars().all() == []


def test_a_local_hero_is_stored_and_sent_as_a_data_uri(session, tmp_path: Path):
    Image.new("RGB", (600, 400), (120, 90, 60)).save(tmp_path / "crumb.png")
    (tmp_path / "loaves.md").write_text(
        "---\ntitle: Three loaves\nhero: ./crumb.png\nhero_credit: 'Photo: Jo Baker'\n---\n"
        "The loaf rested 72 hours at 78% hydration.\n", encoding="utf-8")
    f = facts_from_file(tmp_path / "loaves.md")
    rendered = []
    posts = maker.make(session, f, "article", call=_fake_call(), renderer=_renderer(rendered), now=NOW)
    cover = posts[0].slides[0]["spec"]
    assert cover["image"]["asset_id"] and cover["image"]["url"].endswith(f"/media/{cover['image']['asset_id']}")
    assert rendered[0]["image"]["url"].startswith("data:image/jpeg;base64,")
    assert rendered[0]["image"]["credit"] == "Photo: Jo Baker" and "asset_id" not in rendered[0]["image"]
    hero = session.get(ContentAsset, __import__("uuid").UUID(cover["image"]["asset_id"]))
    assert hero.provenance == {"source": "file", "file": "crumb.png", "credit": "Photo: Jo Baker", "licence": None,
                               "generated": False}
    slide = session.get(ContentAsset, __import__("uuid").UUID(posts[0].slides[0]["asset_id"]))
    assert slide.provenance["composited_from"] == [str(hero.id)] and slide.provenance["credits"] == ["Photo: Jo Baker"]
    assert rendered[-1]["type"] == "story" and rendered[-1]["image"]["url"].startswith("data:")


def test_a_story_only_template_files_one_post_per_frame(session):
    posts = maker.make(session, _facts(extra={"frames": 2}), "story-only", call=_fake_call(), renderer=_renderer(),
                       now=NOW)
    assert [p.kind for p in posts] == ["story", "story"] and [p.title for p in posts] == \
        ["Three loaves, one long rest (1/2)", "Three loaves, one long rest (2/2)"]
    assert posts[1].publish_at > posts[0].publish_at and all(p.caption == "" for p in posts)


def test_a_renderer_failure_leaves_nothing_in_the_queue(session):
    with pytest.raises(maker.RenderError, match=r"rendering a cover slide failed \(500\)"):
        maker.make(session, _facts(), "article", call=_fake_call(), renderer=_renderer(fail=500), now=NOW)
    assert session.execute(select(SocialPost)).scalars().all() == []


def test_revise_re_renders_only_the_changed_slide(session):
    rendered = []
    post, _ = maker.make(session, _facts(), "article", call=_fake_call(), renderer=_renderer(rendered), now=NOW)
    before = [s["asset_id"] for s in post.slides]
    rendered.clear()
    out = maker.revise(session, post.id, slides=[{"index": 1, "fields": {"headline": "Cold dough waits"}}],
                       caption=post.caption + "\n\nEdited.", renderer=_renderer(rendered))
    after = [s["asset_id"] for s in out.slides]
    assert [s["type"] for s in rendered] == ["statement"] and rendered[0]["headline"] == "Cold dough waits"
    assert after[1] != before[1] and [a for i, a in enumerate(after) if i != 1] == \
        [a for i, a in enumerate(before) if i != 1]
    assert out.slides[1]["spec"]["headline"] == "Cold dough waits" and out.caption.endswith("Edited.")

    # A number the post's sources do not have is refused before anything renders.
    rendered.clear()
    with pytest.raises(maker.MakerError, match="slide 2: body '96 hours'"):
        maker.revise(session, post.id, slides=[{"index": 1, "fields": {"body": "96 hours"}}],
                     renderer=_renderer(rendered))
    assert rendered == []
    with pytest.raises(queue.QueueError, match="out of range"):
        maker.revise(session, post.id, slides=[{"index": 9, "fields": {"body": "x"}}], renderer=_renderer())
    queue.approve(session, post.id, publish_at=NOW, approved_by="admin")
    with pytest.raises(queue.QueueError, match="post is approved"):
        maker.revise(session, post.id, slides=[{"index": 1, "fields": {"body": "x"}}], renderer=_renderer())


def test_theme_change_rerenders_each_stored_slide_during_revision(session):
    post, _ = maker.make(session, _facts(), "article", call=_fake_call(), renderer=_renderer(), now=NOW)
    rendered = []
    renderer = _renderer(rendered)
    renderer.theme = "vigil"
    assert make_mod.needs_theme_refresh(session, post, "vigil")
    out = maker.revise(session, post.id, slides=[{"index": 1, "fields": {"headline": "Cold dough waits"}}],
                       renderer=renderer, refresh_theme=True)
    assert len(rendered) == len(post.slides)
    assert out.slides[1]["spec"]["headline"] == "Cold dough waits"


# ---------------------------------------------------------------- the API and the CLI


@pytest.fixture
def admin(session):
    with TestClient(create_app(), base_url="https://postdesk.example") as c:
        r = c.post("/api/auth/setup", json={"password": "correct horse battery"})
        assert r.status_code == 200
        yield c


def test_the_api_shows_a_slides_words_and_patches_them(session, admin, monkeypatch):
    rendered = []
    post, _ = maker.make(session, _facts(), "article", call=_fake_call(), renderer=_renderer(rendered), now=NOW)
    monkeypatch.setattr(make_mod, "Renderer", lambda *a, **k: _renderer(rendered))
    got = admin.get(f"/api/posts/{post.id}").json()
    assert got["slides"][1]["words"]["headline"] == "Three loaves, one"  # headline_em finishes it
    assert got["slides"][1]["spec"]["type"] == "statement"

    rendered.clear()
    r = admin.patch(f"/api/posts/{post.id}",
                    json={"slides": [{"index": 1, "fields": {"headline": "Cold dough waits"}}]})
    assert r.status_code == 200, r.text
    assert r.json()["slides"][1]["words"]["headline"] == "Cold dough waits" and len(rendered) == 1

    r = admin.patch(f"/api/posts/{post.id}", json={"slides": [{"index": 1, "fields": {"body": "96 hours"}}]})
    assert r.status_code == 422 and "96 hours" in r.json()["detail"]
    r = admin.patch(f"/api/posts/{post.id}", json={"slides": [{"index": 1, "fields": {"image": "x"}}]})
    assert r.status_code == 422 and "editable words" in r.json()["detail"]

    admin.post(f"/api/posts/{post.id}/approve", json={"publish_at": NOW.isoformat()})
    r = admin.patch(f"/api/posts/{post.id}", json={"slides": [{"index": 1, "fields": {"headline": "x"}}]})
    assert r.status_code == 409

    # An uploaded slide has no spec, so it cannot be re-rendered.
    up = admin.post("/api/assets", files={"file": ("x.jpg", _jpeg(), "image/jpeg")}, data={"credit": "Me"}).json()
    hand = admin.post("/api/posts", json={"kind": "image", "title": "Hand", "caption": "Hi.",
                                          "slides": [{"asset_id": up["id"], "alt_text": "A"}]}).json()
    assert hand["slides"][0]["spec"] is None and hand["slides"][0]["words"] is None
    r = admin.patch(f"/api/posts/{hand['id']}", json={"slides": [{"index": 0, "fields": {"headline": "x"}}]})
    assert r.status_code == 409 and "no stored spec" in r.json()["detail"]


def test_the_cli_make_command(session, tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from postdesk import cli

    (tmp_path / "loaves.md").write_text(
        "---\ntitle: Three loaves, one long rest\ndate: 2031-03-03\nurl: https://example.com/x\n---\n"
        "The loaf rested 72 hours at 78% hydration.\n", encoding="utf-8")
    runner = CliRunner()
    dry = runner.invoke(cli.app, ["make", str(tmp_path / "loaves.md"), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "template article: carousel, 6 slide(s)" in dry.output and "Slot 1 (cover)" in dry.output
    assert "Dry run" in dry.output and session.execute(select(SocialPost)).scalars().all() == []

    bad = runner.invoke(cli.app, ["make", str(tmp_path / "loaves.md"), "--template", "nope"])
    assert bad.exit_code == 1 and "no template 'nope'" in bad.output

    rendered = []
    monkeypatch.setattr(make_mod, "Renderer", lambda *a, **k: _renderer(rendered))
    real_make = maker.make

    def fake_make(session, facts, tpl, **kw):
        return real_make(session, facts, tpl, call=_fake_call(), **kw)

    monkeypatch.setattr(maker, "make", fake_make)
    result = runner.invoke(cli.app, ["make", str(tmp_path / "loaves.md"), "--template", "explainer"])
    assert result.exit_code == 0, result.output
    assert "Queued as drafts:" in result.output and "carousel" in result.output and "story" in result.output
    assert len(rendered) == 6
    assert sorted(p.kind for p in session.execute(select(SocialPost)).scalars()) == ["carousel", "story"]

    (tmp_path / "slide.json").write_text(json.dumps({"type": "cta", "kicker": "k", "headline": "Read the",
                                                     "headline_em": "method.", "body": "b"}), encoding="utf-8")
    monkeypatch.setattr(maker, "Renderer", lambda *a, **k: _renderer())
    out = runner.invoke(cli.app, ["render", "--spec", str(tmp_path / "slide.json"), "--out", str(tmp_path / "s.jpg")])
    assert out.exit_code == 0, out.output
    assert (tmp_path / "s.jpg").read_bytes()[:2] == b"\xff\xd8"

    listing = runner.invoke(cli.app, ["templates"])
    assert listing.exit_code == 0 and "article" in listing.output and "story-only" in listing.output


def test_doctor_checks_the_renderer(monkeypatch):
    from postdesk import doctor

    monkeypatch.setattr(settings, "renderer_url", None)
    c = doctor.check_renderer()
    assert not c.ok and c.warn and "RENDERER_URL" in c.detail
    monkeypatch.setattr(settings, "renderer_url", "http://renderer.test")
    c = doctor.check_renderer(_renderer())
    assert c.ok and "version 0.1.0" in c.detail and "default" in c.detail, c
    c = doctor.check_renderer(_renderer(fail=401))
    assert not c.ok and not c.warn and "401" in c.detail
    monkeypatch.setattr(config.theme, "name", "mine")
    c = doctor.check_renderer(_renderer())
    assert not c.ok and "'mine' is not among them" in c.detail
