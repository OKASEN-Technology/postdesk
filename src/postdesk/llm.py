"""
How Claude is called (docs/roadmap.md, the agents). One shape for every
agent turn: `messages.create` with the agent's system prompt cached, the
gathered context as the user turn, and structured output (the agent's JSON
schema in `output_config.format`), so the reply is always one JSON object.

- The model and effort per agent come from `[llm]` in postdesk.toml.
- Transient failures (rate limit, connection, 5xx, overloaded) are retried
  with backoff; anything else is an `LLMError` that says what happened.
- A refusal or a reply cut off at `max_tokens` is a failed turn, never a
  retry that spends again.
- Images go in as content blocks (`image_block`): a URL the API fetches,
  or the bytes of a stored file.
- The partner's turn may carry the server-side web search tool
  (`web_search_tool`); a turn the server pauses (`pause_turn`) is resumed,
  `MAX_CONTINUATIONS` times at most, and its searches are costed.
- Every call is logged on `agent_run` with its tokens and cost; a month
  whose runs add up to more than `[llm].monthly_usd` stops every agent
  until the month turns or the ceiling is raised.

No agent reads this module's output as instructions: source text goes in
as data, the schema says what comes out.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import UTC, datetime

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIError,
    APIStatusError,
    InternalServerError,
    OverloadedError,
    RateLimitError,
    ServiceUnavailableError,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from postdesk.config import config, settings
from postdesk.db.models import AgentRun

# USD per million tokens: (input, output). Cache writes cost 1.25x input,
# cache reads 0.1x; `price_of` matches a dated id ("claude-sonnet-5-5-2026...")
# by prefix. An unknown model is logged with no cost, and `doctor` says so.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-fable-5-1": (4.0, 20.0),
}
# Standard API prices per million tokens (input, output, cached input).
OPENAI_PRICES = {
    "gpt-6.1-sol": (2.0, 10.0, 0.10),
    "gpt-6.1-terra": (1.0, 5.0, 0.05),
    "gpt-6-luna": (0.10, 0.50, 0.01),
}
CACHE_WRITE = 1.25
CACHE_READ = 0.1
MAX_TOKENS = 16000
RETRIES = 4
MAX_CONTINUATIONS = 4
WEB_SEARCH_TOOL = "web_search_20260209"
WEB_SEARCH_USD = 10 / 1000  # per search
_RETRYABLE = (RateLimitError, APIConnectionError, InternalServerError, OverloadedError, ServiceUnavailableError)


class LLMError(RuntimeError):
    """A turn that produced nothing usable: no key, over budget, a refusal,
    a reply cut off, invalid JSON, or an API error that is not transient."""


class BudgetExceeded(LLMError):
    pass


class LLMTransient(LLMError):
    """The API stayed unreachable or overloaded through every retry: worth
    trying again later (the job runner does), unlike every other LLMError."""


def price_of(model: str) -> tuple[float, float] | None:
    for prefix, p in sorted(PRICES.items(), key=lambda kv: -len(kv[0])):
        if model == prefix or model.startswith(prefix + "-"):
            return p
    return None


def cost_usd(model: str, usage) -> float | None:
    """What one reply cost, from its `usage` block."""
    p = price_of(model)
    if p is None:
        return None
    inp, out = p
    read = getattr(usage, "cache_read_input_tokens", None) or 0
    write = getattr(usage, "cache_creation_input_tokens", None) or 0
    total = (usage.input_tokens * inp + usage.output_tokens * out
             + read * inp * CACHE_READ + write * inp * CACHE_WRITE)
    return round(total / 1_000_000 + searches_in(usage) * WEB_SEARCH_USD, 6)


def searches_in(usage) -> int:
    stu = getattr(usage, "server_tool_use", None)
    return int(getattr(stu, "web_search_requests", None) or 0) if stu is not None else 0


def image_block(*, url: str | None = None, data: bytes | None = None, media_type: str = "image/jpeg") -> dict:
    """An image content block: by URL (the API fetches it) or by bytes."""
    if data is not None:
        return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                            "data": base64.b64encode(data).decode()}}
    if url and url.startswith("data:image/"):
        head, _, b64 = url.partition(",")
        return {"type": "image", "source": {"type": "base64", "media_type": head[5:].split(";")[0], "data": b64}}
    if not url or not url.startswith(("http://", "https://")):
        raise LLMError(f"an image needs an http(s) URL or its bytes ({url!r} given)")
    return {"type": "image", "source": {"type": "url", "url": url}}


def web_search_tool(allowed_domains: list[str], *, max_uses: int = 5) -> dict:
    """The server-side web search tool, limited to `allowed_domains`."""
    return {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_uses,
            "allowed_domains": sorted({d.strip().lower() for d in allowed_domains if d and d.strip()})}


def model_for(agent: str) -> tuple[str, str]:
    """(model id, effort) for an agent from `[llm]`; an agent the config
    does not name gets the maker's."""
    m = getattr(config.llm, agent, None) or config.llm.maker
    return m.model, m.effort


def month_spend(session: Session, now: datetime | None = None) -> float:
    """What this month's runs have cost so far (UTC months)."""
    now = now or datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = session.execute(select(func.sum(AgentRun.cost_usd)).where(AgentRun.started_at >= start)).scalar_one()
    return float(total or 0)


def check_budget(session: Session, now: datetime | None = None) -> float:
    spent = month_spend(session, now)
    if spent >= config.llm.monthly_usd:
        raise BudgetExceeded(f"this month's agent runs have cost ${spent:.2f}, over the ${config.llm.monthly_usd:.2f} "
                             "ceiling in postdesk.toml [llm].monthly_usd; raise it or wait for the month to turn")
    return spent


def _client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set in .env; the maker needs it (postdesk doctor)")
    # Our own retry loop decides what is transient, so the SDK's is off.
    return Anthropic(api_key=settings.anthropic_api_key, max_retries=0)


def _log(session: Session | None, run: AgentRun) -> None:
    if session is None:
        return
    run.finished_at = datetime.now(UTC)
    session.add(run)
    session.commit()


def _create(client: Anthropic, kwargs: dict, agent: str, run: AgentRun, session: Session | None):
    """One request, with backoff on transient errors."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            return client.messages.create(**kwargs)
        except _RETRYABLE as e:
            last = e
            if attempt < RETRIES - 1:
                time.sleep(2**attempt)
        except APIStatusError as e:
            run.error = f"{type(e).__name__} (HTTP {e.status_code}): {e.message}"[:500]
            _log(session, run)
            raise LLMError(f"the {agent} call failed: {run.error}") from e
        except APIError as e:
            run.error = f"{type(e).__name__}: {e}"[:500]
            _log(session, run)
            raise LLMError(f"the {agent} call failed: {run.error}") from e
    assert last is not None
    run.error = f"{type(last).__name__} after {RETRIES} tries: {last}"[:500]
    _log(session, run)
    raise LLMTransient(f"the {agent} call failed ({run.error})") from last


def _add_usage(run: AgentRun, model: str, usage) -> None:
    run.input_tokens += usage.input_tokens
    run.output_tokens += usage.output_tokens
    run.cache_read_tokens += getattr(usage, "cache_read_input_tokens", None) or 0
    run.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", None) or 0
    c = cost_usd(model, usage)
    run.cost_usd = None if c is None else round(float(run.cost_usd or 0) + c, 6)


def reply_text(content) -> str:
    """The structured reply: the text after the last server tool block
    (with web search, the model may write before it searches)."""
    blocks = list(content)
    last_tool = max((i for i, b in enumerate(blocks) if b.type not in ("text", "thinking", "redacted_thinking")),
                    default=-1)
    return "".join(b.text for b in blocks[last_tool + 1:] if b.type == "text")


def structured_call(
    *,
    agent: str,
    system: str,
    messages: list[dict],
    schema: dict,
    model: str | None = None,
    effort: str | None = None,
    max_tokens: int = MAX_TOKENS,
    tools: list[dict] | None = None,
    session: Session | None = None,
    post_id: uuid.UUID | None = None,
    card_id: uuid.UUID | None = None,
    ref: str | None = None,
    client: Anthropic | None = None,
) -> dict:
    """One agent turn: the reply as the dict `schema` describes. `messages`
    is the conversation so far (text and image blocks, earlier replies with
    the corrections sent back). `tools` adds server tools (the partner's web
    search). `session` logs the run and enforces the monthly ceiling;
    without one nothing is logged."""
    default_model, default_effort = model_for(agent)
    model = model or default_model
    effort = effort or default_effort
    if session is not None:
        check_budget(session)
    run = AgentRun(agent=agent, post_id=post_id, card_id=card_id, ref=ref, model=model, effort=effort,
                   started_at=datetime.now(UTC), status="failed", input_tokens=0, output_tokens=0,
                   cache_read_tokens=0, cache_write_tokens=0)
    if model.startswith(("gpt-", "o1", "o3", "o4")):
        from postdesk.openai_llm import structured_openai
        return structured_openai(agent=agent, system=system, messages=messages, schema=schema, model=model,
                                 effort=effort, max_tokens=max_tokens, tools=tools, session=session,
                                 run=run, client=client)
    client = client or _client()
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": list(messages),
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": effort},
    }
    if tools:
        kwargs["tools"] = tools
    reply = None
    for _ in range(MAX_CONTINUATIONS + 1):
        reply = _create(client, kwargs, agent, run, session)
        _add_usage(run, model, reply.usage)
        if reply.stop_reason != "pause_turn":
            break
        # The server paused a long tool loop: send its turn back and it resumes.
        kwargs["messages"] = [*kwargs["messages"], {"role": "assistant", "content": reply.content}]
    assert reply is not None
    if reply.stop_reason == "pause_turn":
        run.error = f"still searching after {MAX_CONTINUATIONS} continuations"
        _log(session, run)
        raise LLMError(f"the {agent} kept searching and never answered; try again with a narrower question")
    if reply.stop_reason == "refusal":
        run.status, run.error = "refused", "the model declined this request"
        _log(session, run)
        raise LLMError(f"the {agent} declined to write this; change the source or the brief and try again")
    if reply.stop_reason == "max_tokens":
        run.error = f"reply cut off at {max_tokens} tokens"
        _log(session, run)
        raise LLMError(f"the {agent}'s reply was cut off at {max_tokens} tokens; the source is too long for one turn")
    text = reply_text(reply.content)
    try:
        out = json.loads(text)
    except ValueError as e:
        run.error = f"reply was not JSON: {text[:200]!r}"
        _log(session, run)
        raise LLMError(f"the {agent}'s reply was not the JSON asked for") from e
    if not isinstance(out, dict):
        run.error = "reply was not a JSON object"
        _log(session, run)
        raise LLMError(f"the {agent}'s reply was not the JSON object asked for")
    run.status = "ok"
    _log(session, run)
    return out
