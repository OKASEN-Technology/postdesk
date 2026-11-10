"""The HTTP API: the login, the queue endpoints, uploads and the public
media endpoint, through FastAPI's TestClient against the test database."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from postdesk.config import settings
from postdesk.main import create_app


def _jpeg(size=(1080, 1350), colour=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def client(session):
    with TestClient(create_app(), base_url="https://postdesk.example") as c:
        yield c


@pytest.fixture
def admin(client):
    """A client signed in as the admin, after setting the password."""
    r = client.post("/api/auth/setup", json={"password": "correct horse battery"})
    assert r.status_code == 200 and r.json() == {"configured": True, "authenticated": True}
    return client


def _upload(client, name="01.png", colour=(200, 30, 30), **data):
    return client.post("/api/assets", files={"file": (name, _jpeg(colour=colour), "image/jpeg")}, data=data)


def _post(client, n=2, **extra):
    slides = [{"asset_id": _upload(client, f"{i}.png", credit=f"Photo {i}").json()["id"], "alt_text": f"Slide {i}"}
              for i in range(n)]
    body = {"kind": "carousel" if n > 1 else "image", "title": "A post", "caption": "Hello.", "slides": slides, **extra}
    r = client.post("/api/posts", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_setup_then_login_then_logout(client):
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
    assert client.get("/api/posts").status_code == 401
    assert client.post("/api/auth/login", json={"password": "x"}).status_code == 409
    assert client.post("/api/auth/setup", json={"password": "short"}).status_code == 400

    r = client.post("/api/auth/setup", json={"password": "correct horse battery"})
    assert r.status_code == 200 and "postdesk_session" in r.cookies
    assert client.get("/api/posts").status_code == 200
    assert client.post("/api/auth/setup", json={"password": "again again"}).status_code == 409

    client.post("/api/auth/logout")
    assert client.get("/api/auth/status").json()["authenticated"] is False
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "correct horse battery"}).status_code == 200
    assert client.get("/api/posts").status_code == 200


def test_env_password_and_bearer_token(client, monkeypatch):
    monkeypatch.setattr(settings, "postdesk_admin_password", "from-the-env")
    monkeypatch.setattr(settings, "postdesk_api_token", "cli-token")
    assert client.get("/api/auth/status").json()["configured"] is True
    assert client.post("/api/auth/setup", json={"password": "whatever!"}).status_code == 409
    assert client.get("/api/posts", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/posts", headers={"Authorization": "Bearer cli-token"}).status_code == 200
    assert client.post("/api/auth/login", json={"password": "from-the-env"}).status_code == 200


def test_upload_creates_a_jpeg_asset_with_provenance(admin):
    r = admin.post("/api/assets", files={"file": ("x.png", _png_bytes(), "image/png")}, data={"credit": "Me"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_type"] == "image/jpeg" and body["storage"] == "local"
    assert (body["width"], body["height"]) == (1080, 1350)
    assert body["url"] == f"https://postdesk.example/media/{body['id']}"
    # The same bytes again reuse the row.
    again = admin.post("/api/assets", files={"file": ("x.png", _png_bytes(), "image/png")}).json()
    assert again["id"] == body["id"]
    assert admin.post("/api/assets", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (1080, 1350), (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_media_serves_only_referenced_assets(admin, client):
    orphan = _upload(admin, "orphan.png", colour=(1, 2, 3), credit="x").json()
    post = _post(admin)
    slide = post["slides"][0]

    anonymous = TestClient(create_app(), base_url="https://postdesk.example")
    r = anonymous.get(f"/media/{slide['asset_id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    assert anonymous.get(f"/media/{orphan['id']}").status_code == 404
    assert anonymous.get("/media/00000000-0000-0000-0000-000000000000").status_code == 404


def test_private_s3_serves_referenced_media(admin, monkeypatch):
    from postdesk.storage import s3

    for name in ("storage_s3_endpoint", "storage_s3_bucket", "storage_s3_access_key_id",
                 "storage_s3_secret_access_key"):
        monkeypatch.setattr(settings, name, "configured")
    monkeypatch.setattr(settings, "storage_s3_public_base_url", None)
    objects = {}
    monkeypatch.setattr(s3, "put", lambda sha, data, content_type: objects.update({sha: data}))
    monkeypatch.setattr(s3, "get", lambda sha: objects[sha])
    monkeypatch.setattr(s3, "has", lambda sha: sha in objects)
    orphan = _upload(admin, colour=(1, 2, 3)).json()
    post = _post(admin, n=1)
    assert orphan["storage"] == "s3"
    anonymous = TestClient(create_app(), base_url="https://postdesk.example")
    assert anonymous.get(f"/media/{orphan['id']}").status_code == 404
    response = anonymous.get(f"/media/{post['slides'][0]['asset_id']}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_queue_endpoints_and_immutability(admin):
    post = _post(admin)
    assert post["status"] == "draft" and post["publish_at"] is not None
    pid = post["id"]
    assert pid in {r["id"] for r in admin.get("/api/posts").json()}
    assert admin.get("/api/posts", params={"status": "done"}).json() == []

    r = admin.patch(f"/api/posts/{pid}", json={"caption": "Edited.", "alt_texts": ["One", "Two"], "title": "New"})
    assert r.status_code == 200 and r.json()["caption"] == "Edited." and r.json()["title"] == "New"
    assert [s["alt_text"] for s in r.json()["slides"]] == ["One", "Two"]

    when = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    r = admin.post(f"/api/posts/{pid}/approve", json={"publish_at": when})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["approved_by"] == "admin"
    r = admin.patch(f"/api/posts/{pid}", json={"caption": "Sneaky."})
    assert r.status_code == 409 and "needs draft" in r.json()["detail"]

    assert admin.post(f"/api/posts/{pid}/unapprove").json()["status"] == "draft"
    assert admin.post(f"/api/posts/{pid}/reject").json()["status"] == "rejected"
    assert admin.get("/api/posts", params={"status": "done"}).json()[0]["id"] == pid
    assert admin.post(f"/api/posts/{pid}/back-to-draft").status_code == 409
    assert admin.get("/api/posts/00000000-0000-0000-0000-000000000000").status_code == 404

    one = [{"asset_id": post["slides"][0]["asset_id"]}]
    bad = admin.post("/api/posts", json={"kind": "carousel", "title": "x", "slides": one})
    assert bad.status_code == 400 and "carousel takes" in bad.json()["detail"]


def test_list_is_paged_and_has_no_captions(admin):
    ids = [_post(admin)["id"] for _ in range(3)]
    full = admin.get("/api/posts")
    first = admin.get("/api/posts", params={"limit": 2})
    rest = admin.get("/api/posts", params={"limit": 200, "offset": 2})
    assert first.headers["X-Total-Count"] == "3"
    assert [r["id"] for r in first.json() + rest.json()] == [r["id"] for r in full.json()]
    row = full.json()[0]
    assert set(ids) == {r["id"] for r in full.json()}
    assert "caption" not in row and row["slide_count"] == 2 and row["first_slide_url"].startswith("https://postdesk.example/media/")


def test_suggest_times_and_lint(admin):
    post = _post(admin)
    r = admin.post("/api/posts/suggest-times")
    assert r.status_code == 200 and r.json() == []  # every draft already has a time
    r = admin.post("/api/posts/suggest-times", params={"overwrite": "true"})
    assert [p["id"] for p in r.json()] == [post["id"]]
    caption = "A game-changer " + " ".join(f"#t{i}" for i in range(7))
    lint = admin.post("/api/posts/lint-caption", json={"caption": caption})
    assert lint.json()["hashtags"] == 7 and len(lint.json()["warnings"]) == 2 and lint.json()["errors"] == []


def test_config_and_health(admin):
    cfg = admin.get("/api/config").json()
    assert cfg["timezone"] == "Europe/London" and cfg["publisher_hours"] == ["06:00", "21:00"]
    assert cfg["max_hashtags"] == 5 and cfg["storage"] == "local"
    assert admin.get("/api/health").json()["ok"] is True
    # The web app owns every other path; unbuilt, a note is served.
    assert admin.get("/posts/abc").status_code == 200


def test_reel_video_stream_supports_ranges(admin, session):
    from postdesk import queue, storage

    data = b"0123456789"
    asset = storage.store_asset(session, data=data, filename="clip.mp4", content_type="video/mp4")
    queue.create_post(session, kind="reel", title="clip", slides=[{"asset_id": str(asset.id)}])
    url = f"/media/{asset.id}"
    response = admin.get(url, headers={"range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert admin.get(url, headers={"range": "bytes=-3"}).content == b"789"
    assert admin.get(url, headers={"range": "bytes=99-"}).status_code == 416
    assert admin.get(url).content == data


def test_brand_library_connection_and_selection_api(admin, monkeypatch):
    from cryptography.fernet import Fernet

    from postdesk import brand_library

    monkeypatch.setattr(settings, "postdesk_credentials_key", Fernet.generate_key().decode())
    sample = [{"key": "vigil/helmet.jpg", "name": "helmet.jpg", "url": "https://assets.example/vigil/helmet.jpg",
               "type": "image", "size": 20}]
    monkeypatch.setattr(brand_library, "objects", lambda values: sample)
    values = {"account_id": "a" * 32, "bucket": "brand-bucket", "prefix": "vigil/",
              "public_url": "https://assets.example/vigil/", "access_key_id": "example-key",
              "secret_access_key": "example-secret"}
    result = admin.put("/api/settings/brand-library", json=values)
    assert result.status_code == 200
    assert "example-secret" not in result.text and "example-key" not in result.text
    assert result.json()["items"][0]["enabled"] is True
    result = admin.put("/api/settings/brand-library/selection", json={"excluded": ["vigil/helmet.jpg"]})
    assert result.status_code == 200 and result.json()["items"][0]["enabled"] is False
    bad = admin.put("/api/settings/brand-library/selection", json={"excluded": ["unknown"]})
    assert bad.status_code == 400
