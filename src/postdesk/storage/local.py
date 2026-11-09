"""Files on the app's disk, under `settings.postdesk_data_dir / uploads`."""

from __future__ import annotations

from pathlib import Path

from postdesk.config import settings


def root() -> Path:
    return Path(settings.postdesk_data_dir) / "uploads"


def path_for(sha256: str) -> Path:
    return root() / sha256[:2] / sha256


def put(sha256: str, data: bytes) -> None:
    dest = path_for(sha256)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def get(sha256: str) -> bytes:
    return path_for(sha256).read_bytes()
