from cryptography.fernet import Fernet

from postdesk import brand_library
from postdesk.config import settings


class Pager:
    def paginate(self, **kwargs):
        assert kwargs["Bucket"] == "brand-bucket"
        assert kwargs["Prefix"] == "vigil/"
        return [{"Contents": [{"Key": "vigil/helmet 1.jpg", "Size": 20},
                              {"Key": "vigil/demo.mp4", "Size": 30},
                              {"Key": "vigil/readme.txt", "Size": 40}]},
                {"Contents": [{"Key": "vigil/tool.png", "Size": 5}]}]


class Client:
    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return Pager()


def test_connect_discovers_pages_and_saves_keys_encrypted(session, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    monkeypatch.setattr(brand_library, "_connection", lambda v: Client())
    inputs = {"account_id": "a" * 32, "bucket": "brand-bucket", "prefix": "vigil/",
              "public_url": "https://assets.example.com/vigil/", "access_key_id": "access",
              "secret_access_key": "secret-value"}
    items = brand_library.save(session, inputs)
    assert len(items) == 3
    assert items[0]["url"] == "https://assets.example.com/vigil/helmet%201.jpg"
    from postdesk import store
    assert "secret-value" not in store.get(session, brand_library.KEY)
    assert len(brand_library.available(session)) == 3
    store.put(session, brand_library.EXCLUDED_KEY, ["vigil/helmet 1.jpg"])
    enabled = brand_library.available(session)
    assert enabled[0]["enabled"] is False
    candidates = brand_library.select_for_card(enabled, "helmet")
    assert len(candidates) == 1 and candidates[0]["url"].endswith("tool.png")


def test_read_failure_does_not_overwrite_connection(session, monkeypatch):
    import pytest

    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    monkeypatch.setattr(brand_library, "_connection", lambda v: Client())
    v = {"account_id": "a" * 32, "bucket": "brand-bucket", "prefix": "vigil/",
         "public_url": "https://assets.example.com/vigil/", "access_key_id": "access", "secret_access_key": "secret"}
    brand_library.save(session, v)
    v["public_url"] = "https://dash.cloudflare.com/bucket"
    with pytest.raises(ValueError, match="public HTTPS"):
        brand_library.save(session, v)
    assert brand_library.read(session)["public_url"] == "https://assets.example.com/vigil/"


def test_video_validation_checks_codec_and_duration(monkeypatch):
    import json
    from types import SimpleNamespace

    import pytest

    from postdesk.instagram import video

    metadata = {"format": {"format_name": "mov,mp4", "duration": "9"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                            {"codec_type": "audio", "codec_name": "aac"}]}
    monkeypatch.setattr(video.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(metadata)))
    assert video.validate_video(b"video") == (1080, 1920)
    metadata["streams"][0]["codec_name"] = "vp9"
    with pytest.raises(ValueError, match=r"H\.264"):
        video.validate_video(b"video")
