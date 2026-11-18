"""Optional OpenAI image generation for a single existing slide."""

import base64

from openai import OpenAI

from postdesk.config import settings


def generate(prompt: str) -> bytes:
    if not settings.image_generation_enabled or not settings.openai_api_key:
        raise ValueError("Image generation is not configured")
    if not prompt.strip() or len(prompt) > 4000:
        raise ValueError("Invalid image prompt")
    response = OpenAI(api_key=settings.openai_api_key, timeout=120, max_retries=0).images.generate(
        model=settings.image_generation_model,
        prompt=("Create an editorial illustration with no text, lettering, logos, or factual claims. " + prompt),
        n=1,
        size="1024x1024",
        quality="low",
    )
    if not response.data or not response.data[0].b64_json:
        raise ValueError("Image provider returned no image")
    return base64.b64decode(response.data[0].b64_json, validate=True)
