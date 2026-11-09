"""Instagram publishing (postdesk.instagram). Pure: the Graph API is faked
with httpx.MockTransport; no database, no network."""

from pathlib import Path

import httpx
import pytest
from PIL import Image as PILImage

from postdesk import instagram as ig
from tests.conftest import FakeGraph, graph_for

CAPTION = """Three loaves, one weekend.

The recipe is at the link in bio ↓

#homebaking #sourdough

---
ALT TEXT
1: Cover slide.
2: Second slide.
"""


def _png(tmp_path: Path, name: str, size=(1080, 1350), mode="RGB") -> Path:
    p = tmp_path / name
    PILImage.new(mode, size, (10, 22, 48) if mode == "RGB" else (10, 22, 48, 255)).save(p)
    return p


def test_parse_caption_file_splits_alt_text():
    caption, alts = ig.parse_caption_file(CAPTION)
    assert caption.startswith("Three loaves") and caption.endswith("#sourdough")
    assert "ALT TEXT" not in caption
    assert alts == {1: "Cover slide.", 2: "Second slide."}


def test_build_post_carousel_converts_to_jpeg(tmp_path):
    paths = [_png(tmp_path, "01.png", mode="RGBA"), _png(tmp_path, "02.png")]
    post = ig.build_post(paths, CAPTION)
    assert post.kind == "carousel"
    assert [s.alt_text for s in post.slides] == ["Cover slide.", "Second slide."]
    assert all(s.jpeg[:2] == b"\xff\xd8" for s in post.slides)
    assert post.warnings == []


def test_build_post_reports_every_problem(tmp_path):
    paths = [_png(tmp_path, "01.png"), _png(tmp_path, "02.png", size=(1080, 1920)), tmp_path / "missing.png"]
    with pytest.raises(ig.InstagramError) as e:
        ig.build_post(paths, "x" * 2300)
    msg = str(e.value)
    assert "missing.png: not found" in msg
    assert "02.png: 1080×1920 is outside" in msg
    assert "2300 characters" in msg


def test_build_post_story_takes_tall_image_and_drops_caption(tmp_path):
    post = ig.build_post([_png(tmp_path, "s.png", size=(1080, 1920))], CAPTION, story=True)
    assert post.kind == "story" and post.caption == ""
    assert any("no caption" in w for w in post.warnings)


def test_lint_caption_warns_from_config():
    errors, warnings = ig.lint_caption("A real game-changer. " + " ".join(f"#t{i}" for i in range(6)))
    assert errors == []
    assert any("6 hashtags" in w and "your limit is 5" in w for w in warnings)
    assert any("retired phrase" in w for w in warnings)
    assert ig.lint_caption(" ".join(f"#t{i}" for i in range(31)))[0] == ["31 hashtags (Instagram max 30)"]


def test_a_rank_is_not_a_hashtag():
    caption = ("Rye is #1 in this week's bakes, up from #4. Spelt went #14 to #2.\n\n"
               "#homebaking #sourdough #ryebread #weekendbake #bakery")
    assert ig.hashtags(caption) == ["#homebaking", "#sourdough", "#ryebread", "#weekendbake", "#bakery"]
    assert ig.lint_caption(caption) == ([], [])
    assert ig.hashtags("#22. #1s #2026bread") == ["#1s", "#2026bread"]


def test_check_public_refuses_local_addresses():
    for url in ("http://localhost:8080/media/x", "http://127.0.0.1/media/x", "http://app.localhost/media/x"):
        with pytest.raises(ig.InstagramError, match="not reachable"):
            ig.check_public(url)
    ig.check_public("https://postdesk.example/media/x")


def test_resolve_account_picks_linked_page():
    graph = graph_for(FakeGraph())
    acct = ig.resolve_account(graph)
    assert (acct.ig_user_id, acct.username, acct.page_name, acct.token) == ("IG1", "bakery_tales", "The Bakery", "PAGE")
    assert ig.publishing_limit(graph, acct) == (2, 100)


def test_resolve_account_prefers_the_page_token(monkeypatch):
    from postdesk.config import settings

    monkeypatch.setattr(settings, "instagram_page_token", "PAGE")
    monkeypatch.setattr(settings, "instagram_user_id", "IG1")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/IG1") and request.url.params["access_token"] == "PAGE"
        return httpx.Response(200, json={"id": "IG1", "username": "bakery_tales"})

    acct = ig.resolve_account(ig.Graph(httpx.Client(transport=httpx.MockTransport(handler))))
    assert acct.token_kind == "page" and acct.page_name is None


def test_publish_carousel_end_to_end():
    fake = FakeGraph()
    graph = graph_for(fake)
    acct = ig.resolve_account(graph)
    items = [("https://postdesk.example/media/a1", "Cover slide."),
             ("https://postdesk.example/media/a2", "Second slide.")]

    result = ig.publish_hosted("carousel", items, "Three loaves.", graph, acct, log=lambda *_: None)

    assert result == {"id": "M1", "permalink": "https://www.instagram.com/p/xyz/"}
    media = [p for m, path, p in fake.calls if m == "POST" and path == "IG1/media"]
    children, carousel = media[:2], media[2]
    assert all(i["is_carousel_item"] == "true" for i in children)
    assert [i["alt_text"] for i in children] == ["Cover slide.", "Second slide."]
    assert carousel["media_type"] == "CAROUSEL" and carousel["children"] == "C1,C2"
    assert carousel["caption"] == "Three loaves."
    assert fake.polls == {"C1": 2, "C2": 2, "C3": 2}  # each polled until FINISHED
    publish_call = next(p for m, path, p in fake.calls if path.endswith("/media_publish"))
    assert publish_call["creation_id"] == "C3"


def test_graph_error_surfaces_message():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token."}})

    graph = ig.Graph(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ig.InstagramError, match="Invalid OAuth access token"):
        ig.resolve_account(graph)


def test_an_image_story_never_sends_alt_text_but_feed_media_does():
    acct = ig.resolve_account(graph_for(FakeGraph()))
    url = "https://postdesk.example/media/c1"
    sent = {}
    for kind, items in [("story", [(url, "The Story frame.")]), ("image", [(url, "One image.")]),
                        ("carousel", [(url, "Cover."), (url, "Second.")])]:
        fake = FakeGraph()
        ig.publish_hosted(kind, items, "Words.", graph_for(fake), acct, log=lambda *_: None)
        sent[kind] = [p for m, path, p in fake.calls if m == "POST" and path == "IG1/media"]
    assert sent["story"][0] == {"media_type": "STORIES", "image_url": url, "access_token": "PAGE"}
    assert sent["image"][0]["alt_text"] == "One image." and sent["image"][0]["caption"] == "Words."
    assert [p.get("alt_text") for p in sent["carousel"]] == ["Cover.", "Second.", None]


def test_reels_are_refused_for_now():
    acct = ig.resolve_account(graph_for(FakeGraph()))
    with pytest.raises(ig.InstagramError, match="reels"):
        ig.publish_hosted("reel", [("https://postdesk.example/media/v", "")], "", graph_for(FakeGraph()), acct)


def test_container_error_is_reported():
    class Broken(FakeGraph):
        def __call__(self, request):
            path = request.url.path.split("/", 2)[2]
            if path == "C1":
                return httpx.Response(200, json={"status_code": "ERROR", "status": "Media not fetchable"})
            return super().__call__(request)

    acct = ig.resolve_account(graph_for(Broken()))
    with pytest.raises(ig.InstagramError, match="Media not fetchable"):
        ig.publish_hosted("image", [("https://postdesk.example/media/x", "")], "Hi", graph_for(Broken()), acct)
