import httpx

from postdesk import brand_assets
from postdesk.agents import partner
from postdesk.board import facts
from postdesk.config import config
from tests import boardkit as kit


def test_public_index_builds_candidates_and_rejects_escaping_paths(monkeypatch):
    monkeypatch.setattr(brand_assets, "_public_host", lambda host: True)
    real_client = httpx.Client
    payload = {"images": [{"path": "helmet.jpg", "description": "Our helmet"}]}

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)), **kwargs)

    monkeypatch.setattr(brand_assets.httpx, "Client", client)
    images, error = brand_assets.load(config_url="https://assets.example.com/vigil")
    assert error is None
    assert images[0]["url"] == "https://assets.example.com/vigil/helmet.jpg"
    assert images[0]["what"] == "Our helmet"
    payload["images"][0]["path"] = "../private.jpg"
    assert brand_assets.load(config_url="https://assets.example.com/vigil")[1]


def test_selected_brand_image_reaches_the_maker(session, monkeypatch):
    monkeypatch.setattr(config.voice, "brand_assets_url", "https://assets.example.com/vigil")
    candidate = {"key": "brand-1", "for": "hero", "url": "https://assets.example.com/vigil/helmet.jpg",
                 "asset_id": None, "credit": "Brand asset", "licence": "Owner supplied",
                 "what": "Helmet", "source": "account brand assets"}
    monkeypatch.setattr(brand_assets, "load", lambda **kwargs: ([candidate], None))
    card = kit.card(session)
    call = kit.scripted(kit.partner_reply(use_images=["brand-1"]))
    partner.turn(session, card, call=call)
    assert facts.facts_for_make(card).hero.url == candidate["url"]
    assert "Helmet" in str(call.calls[0]["messages"])


def test_dashboard_url_is_rejected():
    import pytest
    from pydantic import ValidationError

    from postdesk.config import Voice

    with pytest.raises(ValidationError):
        Voice(brand_assets_url="https://dash.cloudflare.com/account/r2/buckets/example")
