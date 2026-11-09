"""
The image sources (postdesk.images): each one's picture is stored as a
content_asset whose provenance names the source, the page it came from,
the licence (or that there is none) and the credit; the licence filter on
Wikimedia; Business Discovery's rule that someone else's post must name the
subject; a permission on file replacing the credit; the partner's lookups
running after its turn; the image permissions API. Wikimedia, the web and
the Graph API are faked.
"""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from postdesk import images, jobs, storage
from postdesk.board import actions, cards
from postdesk.config import config
from postdesk.db.models import Card, ContentAsset
from postdesk.images import business_discovery, page, rights, wikimedia
from postdesk.main import create_app
from tests import boardkit as kit
from tests import telegramkit as tk
from tests.conftest import FakeGraph, graph_for


def _quiet(_m):
    pass


def _asset(session, candidate) -> ContentAsset:
    return session.get(ContentAsset, uuid.UUID(candidate["asset_id"]))


# ---------------------------------------------------------------- fakes


def commons_page(i, *, licence="CC BY 4.0", artist="<a href='/wiki/User:Jo'>Jo Baker</a>", mime="image/jpeg"):
    return {"index": i, "title": f"File:Rye loaf {i}.jpg", "imageinfo": [{
        "url": f"https://upload.wikimedia.org/full/rye{i}.jpg",
        "thumburl": f"https://upload.wikimedia.org/thumb/rye{i}.jpg",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Rye_loaf_{i}.jpg", "mime": mime,
        "extmetadata": {"LicenseShortName": {"value": licence}, "Artist": {"value": artist},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0"},
                        "ImageDescription": {"value": f"A rye loaf, number {i}"}}}]}


class FakeWeb:
    """Commons' API, its image files, and a bakery's site."""

    def __init__(self, pages=None):
        self.pages = pages if pages is not None else [commons_page(1)]
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.seen.append(url)
        if request.url.host == "commons.wikimedia.org":
            assert request.url.params["gsrnamespace"] == "6"
            return httpx.Response(200, json={"query": {"pages": {str(i): p for i, p in enumerate(self.pages)}}})
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, content=tk.jpeg(), headers={"content-type": "image/jpeg"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=(
            '<html><head><title>Rye | Bakes</title><meta property="og:site_name" content="Bakes">'
            '<meta property="og:image" content="/img/rye.jpg"><meta property="og:image:alt" content="A rye loaf">'
            "</head><body><article><h1>Rye</h1><p>Seven loaves.</p></article></body></html>"))


def client(fake) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake))


class FakeDiscovery(FakeGraph):
    """FakeGraph, plus Business Discovery for one account."""

    def __init__(self, bd):
        super().__init__()
        self.bd = bd

    def __call__(self, request):
        if "business_discovery" in request.url.params.get("fields", ""):
            self.calls.append((request.method, "discovery", dict(request.url.params)))
            if self.bd is None:
                return httpx.Response(400, json={"error": {"message": "Invalid user id"}})
            return httpx.Response(200, json={"business_discovery": self.bd, "id": "IG1"})
        return super().__call__(request)


def media(i, caption, kind="IMAGE"):
    m = {"id": f"m{i}", "caption": caption, "media_type": kind, "permalink": f"https://www.instagram.com/p/m{i}/",
         "media_url": f"https://cdn.example/m{i}.jpg"}
    if kind == "CAROUSEL_ALBUM":
        m["children"] = {"data": [{"media_type": "VIDEO"}, {"media_type": "IMAGE",
                                                             "media_url": f"https://cdn.example/m{i}-2.jpg"}]}
    return m


def picker(choice=1, what="the baker at the oven"):
    calls = []

    def call(*, system, messages, schema):
        calls.append(messages)
        return {"choice": choice, "what": what, "why": "it shows the work"}

    call.calls = calls
    return call


def ctx(card, **kw):
    return images.Context(card=card, log=_quiet, **kw)


# ---------------------------------------------------------------- licences


@pytest.mark.parametrize("name,want", [
    ("CC BY 4.0", "CC BY 4.0"), ("CC-BY-2.0", "CC BY 2.0"), ("cc by", "CC BY"), ("CC0", "CC0"),
    ("Public domain", "Public domain"), ("PD-US", "Public domain"),
    ("CC BY-SA 4.0", None), ("CC BY-NC 2.0", None), ("CC BY-ND 3.0", None), ("GFDL", None), ("", None), (None, None),
])
def test_only_open_licences_a_post_may_use_as_is(name, want):
    assert rights.open_licence(name) == want


# ---------------------------------------------------------------- wikimedia


def test_wikimedia_stores_the_picture_with_its_licence_author_and_page(session):
    fake = FakeWeb([commons_page(1, licence="CC BY-SA 4.0"), commons_page(2, mime="image/svg+xml"),
                    commons_page(3), commons_page(4, licence="Public domain", artist="")])
    c = kit.card(session)
    added, problems = images.run_lookups(session, c, [{"source": "wikimedia", "subject": "hero", "query": "rye loaf"}],
                                         ctx(c, client=client(fake)))
    assert problems == []
    assert [a["credit"] for a in added] == ["Photo: Jo Baker / Wikimedia Commons, CC BY 4.0",
                                            "Photo: unknown / Wikimedia Commons"]
    first = added[0]
    assert first["key"] == "found-1" and first["licence"] == "CC BY 4.0" and first["what"] == "A rye loaf, number 3"
    prov = _asset(session, first).provenance
    assert prov["source"] == "wikimedia" and prov["licence"] == "CC BY 4.0" and prov["author"] == "Jo Baker"
    assert prov["permalink"] == "https://commons.wikimedia.org/wiki/File:Rye_loaf_3.jpg"
    assert prov["licence_url"] == "https://creativecommons.org/licenses/by/4.0" and prov["found_by"] == "partner"
    assert "https://upload.wikimedia.org/thumb/rye3.jpg" in fake.seen  # the thumbnail, not the full file
    assert c.plan["lookups"] == 1 and [f["key"] for f in c.plan["found"]] == ["found-1", "found-2"]


def test_wikimedia_with_nothing_open_finds_nothing(session):
    c = kit.card(session)
    added, problems = images.run_lookups(session, c, [{"source": "wikimedia", "subject": "hero", "query": "rye"}],
                                         ctx(c, client=client(FakeWeb([commons_page(1, licence="CC BY-NC 4.0")]))))
    assert added == [] and problems == ["wikimedia found nothing usable for 'rye'"]


# ---------------------------------------------------------------- page


def test_the_page_source_credits_the_site_and_records_no_licence(session):
    c = kit.card(session)  # its source is https://example.com/bakes/three-loaves
    fake = FakeWeb()
    added, problems = images.run_lookups(session, c, [{"source": "page", "subject": "hero", "query": ""}],
                                         ctx(c, client=client(fake)))
    assert problems == []
    (one,) = added
    assert one["credit"] == "Image: Bakes" and one["licence"] is None and one["what"] == "A rye loaf"
    prov = _asset(session, one).provenance
    assert prov["source"] == "page" and prov["licence"] is None
    assert prov["permalink"] == "https://example.com/bakes/three-loaves"
    assert "https://example.com/img/rye.jpg" in fake.seen


def test_the_page_source_only_reads_the_card_s_own_site(session):
    c = kit.card(session)
    added, problems = images.run_lookups(session, c, [{"source": "page", "subject": "hero",
                                                       "query": "https://elsewhere.example/a"}],
                                         ctx(c, client=client(FakeWeb())))
    assert added == [] and "not the source's own site" in problems[0]


def test_a_permission_on_file_for_the_domain_gives_its_credit(session):
    p = rights.add(session, holder="Bakes Ltd", holder_kind="organisation", scope="any image on their site",
                   credit="Photo: Bakes Ltd, used with permission", evidence="email of 2 March", recorded_by="admin",
                   domain="www.example.com")
    c = kit.card(session)
    (one,), _ = images.run_lookups(session, c, [{"source": "page", "subject": "hero", "query": ""}],
                                   ctx(c, client=client(FakeWeb())))
    assert one["credit"] == "Photo: Bakes Ltd, used with permission" and one["permission_id"] == str(p.id)
    assert _asset(session, one).provenance["permission_id"] == str(p.id)


# ---------------------------------------------------------------- business discovery


def test_business_discovery_uses_the_api_and_the_pick_and_credits_the_handle(session):
    bd = {"username": "jo.bakes", "name": "Jo Baker", "media": {"data": [
        media(1, "Flyer: open day Saturday"), media(2, "Proving the rye", "CAROUSEL_ALBUM"),
        media(3, "A reel", "VIDEO")]}}
    graph = FakeDiscovery(bd)
    pick = picker(choice=2)
    c = kit.card(session)
    added, problems = images.run_lookups(session, c, [{"source": "business_discovery", "subject": "Jo Baker",
                                                       "query": "@Jo.Bakes"}],
                                         ctx(c, client=client(FakeWeb()), graph=graph_for(graph), pick=pick))
    assert problems == []
    (one,) = added
    assert one["credit"] == "Photo: @jo.bakes on Instagram" and one["tag"] == "@jo.bakes" and one["licence"] is None
    assert one["for"] == "Jo Baker" and one["what"] == "the baker at the oven"
    prov = _asset(session, one).provenance
    assert prov["source"] == "business_discovery" and prov["permalink"] == "https://www.instagram.com/p/m2/"
    assert prov["url"] == "https://cdn.example/m2-2.jpg" and prov["licence"] is None
    fields = next(p for _, path, p in graph.calls if path == "discovery")["fields"]
    assert fields.startswith("business_discovery.username(jo.bakes){") and "media.limit(18)" in fields
    sent = [b for b in pick.calls[0][0]["content"] if b["type"] == "image"]
    assert len(sent) == 2  # the video is never offered


def test_someone_else_s_post_must_name_the_subject(session):
    bd = {"username": "the_mill", "name": "The Mill", "media": {"data": [
        media(1, "Our new flour"), media(2, "Jo Baker's rye, baked with our flour")]}}
    assert [o["id"] for o in business_discovery.options(bd, "Jo Baker")] == ["m2"]
    assert [o["id"] for o in business_discovery.options(bd, None)] == ["m1", "m2"]
    bd["media"]["data"].pop()
    c = kit.card(session)
    added, problems = images.run_lookups(session, c, [{"source": "business_discovery", "subject": "Jo Baker",
                                                       "query": "the_mill"}],
                                         ctx(c, graph=graph_for(FakeDiscovery(bd)), pick=picker()))
    assert added == [] and "has no recent image post naming Jo Baker" in problems[0]


def test_a_pick_of_none_and_an_account_the_api_cannot_read(session):
    bd = {"username": "jo.bakes", "name": "Jo Baker", "media": {"data": [media(1, "Flyer")]}}
    c = kit.card(session)
    _, problems = images.run_lookups(session, c, [{"source": "business_discovery", "subject": "Jo Baker",
                                                   "query": "jo.bakes"}],
                                     ctx(c, graph=graph_for(FakeDiscovery(bd)), pick=picker(choice=None)))
    assert "none of @jo.bakes's recent posts shows Jo Baker" in problems[0]
    _, problems = images.run_lookups(session, c, [{"source": "business_discovery", "subject": "Jo Baker",
                                                   "query": "private.person"}],
                                     ctx(c, graph=graph_for(FakeDiscovery(None)), pick=picker()))
    assert "no business or creator account @private.person" in problems[0]
    _, problems = images.run_lookups(session, c, [{"source": "business_discovery", "subject": "x",
                                                   "query": "not a handle!"}], ctx(c))
    assert "is not an Instagram handle" in problems[0]


def test_a_permission_for_the_handle_replaces_the_credit_and_tag(session):
    p = rights.add(session, holder="Jo Baker", holder_kind="person", scope="feed posts", credit="Photo: Jo Baker",
                   evidence="DM of 1 March", recorded_by="admin", instagram="@Jo.Bakes", tag="@jo.bakes.studio")
    bd = {"username": "jo.bakes", "name": "Jo Baker", "media": {"data": [media(1, "Rye")]}}
    c = kit.card(session)
    (one,), _ = images.run_lookups(session, c, [{"source": "business_discovery", "subject": "Jo Baker",
                                                 "query": "jo.bakes"}],
                                   ctx(c, client=client(FakeWeb()), graph=graph_for(FakeDiscovery(bd)), pick=picker()))
    assert (one["credit"], one["tag"], one["permission_id"]) == ("Photo: Jo Baker", "@jo.bakes.studio", str(p.id))
    rights.revoke(session, p.id)
    assert rights.covering(session, instagram="jo.bakes") is None


# ---------------------------------------------------------------- attachment


def test_an_attachment_s_record_gets_the_credit_the_partner_used(session):
    c = kit.card(session)
    actions.attach_image(session, c, tk.jpeg(), filename="rye.jpg", note="Photo: Jo Baker, mine to use",
                         actor="admin")
    (att,) = cards.attachments(session, c)
    asset = session.get(ContentAsset, uuid.UUID(att["asset_id"]))
    assert asset.provenance["source"] == "attachment" and asset.provenance["note"] == "Photo: Jo Baker, mine to use"
    used = {"attachment": "attachment-1", "for": "hero", "credit": "Photo: Jo Baker", "what": "a rye loaf on a board"}
    reply = kit.partner_reply(attached_images=[used])
    jobs.run_pending(session, kit.deps(partner=kit.scripted(reply)), log=_quiet)
    session.refresh(asset)
    assert c.column == "Brief ready"
    assert asset.provenance["credit"] == "Photo: Jo Baker" and asset.provenance["what"] == "a rye loaf on a board"
    assert asset.provenance["licence"] is None


def test_attachments_off_refuses_an_upload(session, monkeypatch):
    monkeypatch.setattr(config.images, "sources", ["page"])
    c = kit.card(session)
    with pytest.raises(Exception, match="attached images are off"):
        actions.attach_image(session, c, tk.jpeg(), filename="rye.jpg", note="Photo: me", actor="admin")


# ---------------------------------------------------------------- the partner's lookups


def test_the_partner_asks_sees_what_came_back_and_chooses(session):
    c = kit.card(session)
    jobs.enqueue(session, "partner", c.id)
    ask = kit.partner_reply(column="Planned", brief=None, guidance=None, reply="Looking for a rye picture.",
                            image_lookups=[{"source": "wikimedia", "subject": "hero", "query": "rye loaf"},
                                           {"source": "attachment", "subject": "hero", "query": ""}])
    choose = kit.partner_reply(use_images=["found-1"])
    call = kit.scripted(ask, choose)
    deps = kit.deps(partner=call)
    deps.fetch = client(FakeWeb())
    jobs.run_pending(session, deps, log=_quiet)  # the turn, the lookups, and the turn that chooses
    note = next(m for m in cards.thread(session, c) if m.body_md.startswith("Image lookups"))
    assert note.author == "system" and "found-1: Photo: Jo Baker / Wikimedia Commons, CC BY 4.0" in note.body_md
    assert "`attachment` is not an image source" in note.body_md
    assert c.column == "Brief ready"
    second = call.calls[1]["messages"][0]["content"]
    assert "found-1" in str(second) and "CC BY 4.0" in str(second)
    hero = c.plan["images"][0]
    assert hero["licence"] == "CC BY 4.0" and hero["credit"].startswith("Photo: Jo Baker")


def test_lookups_stop_at_the_limit_and_skip_sources_that_are_off(session, monkeypatch):
    monkeypatch.setattr(config.images, "sources", ["page", "wikimedia"])
    c = kit.card(session)
    c.plan = {**c.plan, "lookups": images.MAX_LOOKUPS - 1}
    session.commit()
    reqs = [{"source": "business_discovery", "subject": "x", "query": "jo"},
            {"source": "wikimedia", "subject": "hero", "query": "rye"},
            {"source": "wikimedia", "subject": "hero", "query": "more rye"}]
    added, problems = images.run_lookups(session, c, reqs, ctx(c, client=client(FakeWeb())))
    assert len(added) == 1 and c.plan["lookups"] == images.MAX_LOOKUPS
    assert "`business_discovery` is not an image source" in problems[0]
    assert problems[-1] == f"no more lookups on this card (the limit is {images.MAX_LOOKUPS})"


def test_a_found_picture_is_served_from_media(session):
    c = kit.card(session)
    (one,), _ = images.run_lookups(session, c, [{"source": "wikimedia", "subject": "hero", "query": "rye"}],
                                   ctx(c, client=client(FakeWeb())))
    with TestClient(create_app(), base_url="https://postdesk.example") as http:
        r = http.get(one["url"].removeprefix("https://postdesk.example"))
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_a_link_filed_from_the_web_keeps_its_page_picture(session):
    c = actions.file_post(session, title="", text="", link="https://bakes.example/rye", column="Planned",
                          kind="post", template=None, actor="admin", client=client(FakeWeb()), log=_quiet)
    hero = c.facts["hero"]
    assert hero["credit"] == "Image: Bakes" and hero["licence"] is None
    asset = session.get(ContentAsset, uuid.UUID(hero["asset_id"]))
    assert asset.provenance["source"] == "page" and asset.provenance["found_by"] == "page"
    assert storage.asset_url(asset.id) == hero["url"]


# ---------------------------------------------------------------- the permissions API


def test_the_permissions_api(session):
    with TestClient(create_app(), base_url="https://postdesk.example") as http:
        http.post("/api/auth/setup", json={"password": "correct horse battery"})
        body = {"holder": "Jo Baker", "holder_kind": "person", "scope": "feed posts", "credit": "Photo: Jo Baker",
                "evidence": "DM of 1 March", "instagram": "jo.bakes"}
        assert http.post("/api/permissions", json={**body, "instagram": None}).status_code == 400
        r = http.post("/api/permissions", json=body)
        assert r.status_code == 200 and r.json()["recorded_by"] == "admin" and r.json()["active"]
        pid = r.json()["id"]
        assert [p["id"] for p in http.get("/api/permissions").json()] == [pid]
        assert http.delete(f"/api/permissions/{pid}").json()["active"] is False
        assert http.get("/api/permissions").json() == []
        assert len(http.get("/api/permissions?revoked=true").json()) == 1
    with pytest.raises(rights.RightsError, match="not an agent"):
        rights.add(session, holder="x", holder_kind="person", scope="s", credit="c", evidence="e",
                   recorded_by="partner", instagram="x")
    assert session.scalars(select(Card)).all() == []
    assert page.picture('<meta name="twitter:image" content="a.jpg">', "https://x.example/p/") == \
        ("https://x.example/p/a.jpg", None)
    assert wikimedia.MAX_RESULTS == 3
