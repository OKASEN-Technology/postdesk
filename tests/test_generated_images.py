from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from postdesk.images import generated


def test_generation_requires_opt_in(monkeypatch):
    monkeypatch.setattr(generated.settings, "image_generation_enabled", False)
    with pytest.raises(ValueError, match="not configured"):
        generated.generate("A forest")


def test_generated_image_uses_bounded_request(monkeypatch):
    monkeypatch.setattr(generated.settings, "image_generation_enabled", True)
    monkeypatch.setattr(generated.settings, "openai_api_key", "test")
    images = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(
        data=[SimpleNamespace(b64_json="aGVsbG8=")])) )
    monkeypatch.setattr(generated, "OpenAI", lambda **kwargs: SimpleNamespace(images=images))
    assert generated.generate("A forest") == b"hello"
    args = images.generate.call_args.kwargs
    assert args["n"] == 1 and args["quality"] == "low"
    assert "no text" in args["prompt"]
