"""X API user-context authentication and publication.

A timeout when creating a post is ambiguous: X may have published it. Callers
must not automatically retry an ambiguous request.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx
from oauthlib.oauth1 import Client as OAuthClient

BASE = "https://api.x.com"
MAX_PHOTOS = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class XError(Exception):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class XCredentials:
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str


class XClient:
    def __init__(self, credentials: XCredentials, *, client: httpx.Client | None = None):
        self.signer = OAuthClient(credentials.api_key, client_secret=credentials.api_secret,
                                  resource_owner_key=credentials.access_token,
                                  resource_owner_secret=credentials.access_secret)
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 publish: bool = False) -> dict:
        url = BASE + path
        _, headers, _ = self.signer.sign(url, http_method=method)
        try:
            response = self.client.request(method, url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise XError("X did not respond. " + ("Check X before retrying; the post may have been published."
                                                  if publish else "Check the connection and try again."),
                         uncertain=publish) from exc
        if not response.is_success:
            # A 5xx can be returned after the request was accepted.
            uncertain = publish and response.status_code >= 500
            raise XError(f"X returned HTTP {response.status_code}. " +
                         ("Check X before retrying; the post may have been published."
                          if uncertain else "Check credentials, access, credits, and post content."),
                         uncertain=uncertain)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except ValueError as exc:
            raise XError("X returned an unreadable response. " +
                         ("Check X before retrying; the post may have been published." if publish else "Try again."),
                         uncertain=publish) from exc

    def whoami(self) -> dict:
        result = self._request("GET", "/2/users/me")
        data = result.get("data")
        if not isinstance(data, dict) or not data.get("id") or not data.get("username"):
            raise XError("X did not return an account. Check the app permissions.")
        return {"id": str(data["id"]), "username": str(data["username"])}

    def upload_image(self, data: bytes) -> str:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise XError("Each X image must be no larger than 5 MB.")
        result = self._request("POST", "/2/media/upload", body={
            "media": base64.b64encode(data).decode("ascii"), "media_category": "tweet_image",
        })
        media_id = (result.get("data") or {}).get("id")
        if not media_id:
            raise XError("X did not return an uploaded image ID.")
        return str(media_id)

    def create_post(self, text: str, media_ids: list[str]) -> str:
        if len(media_ids) > MAX_PHOTOS or (not text.strip() and not media_ids):
            raise XError("An X post needs text or up to four images.")
        body: dict = {"text": text}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        result = self._request("POST", "/2/tweets", body=body, publish=True)
        post_id = (result.get("data") or {}).get("id")
        if not post_id:
            raise XError("X did not return a post ID. Check X before retrying.", uncertain=True)
        return str(post_id)
