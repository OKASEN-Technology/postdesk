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
- Every call is logged on `agent_run` with its tokens and cost; a month
  whose runs add up to more than `[llm].monthly_usd` stops every agent
  until the month turns or the ceiling is raised.

No agent reads this module's output as instructions: source text goes in
as data, the schema says what comes out.
"""

from __future__ import annotations

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
CACHE_WRITE = 1.25
CACHE_READ = 0.1
MAX_TOKENS = 16000
RETRIES = 4
_RETRYABLE = (RateLimitError, APIConnectionError, InternalServerError, OverloadedError, ServiceUnavailableError)


class LLMError(RuntimeError):
    """A turn that produced nothing usable: no key, over budget, a refusal,
    a reply cut off, invalid JSON, or an API error that is not transient."""


class BudgetExceeded(LLMError):
    pass


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
    return round(total / 1_000_000, 6)


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


def structured_call(
    *,
    agent: str,
    system: str,
    messages: list[dict],
    schema: dict,
    model: str | None = None,
    effort: str | None = None,
    max_tokens: int = MAX_TOKENS,
    session: Session | None = None,
    post_id: uuid.UUID | None = None,
    card_id: uuid.UUID | None = None,
    ref: str | None = None,
    client: Anthropic | None = None,
) -> dict:
    """One agent turn: the reply as the dict `schema` describes. `messages`
    is the conversation so far (the user turn, and earlier replies with the
    corrections sent back). `session` logs the run and enforces the monthly
    ceiling; without one nothing is logged."""
    default_model, default_effort = model_for(agent)
    model = model or default_model
    effort = effort or default_effort
    if session is not None:
        check_budget(session)
    run = AgentRun(agent=agent, post_id=post_id, card_id=card_id, ref=ref, model=model, effort=effort,
                   started_at=datetime.now(UTC), status="failed")
    client = client or _client()
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": effort},
    }
    reply = None
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            reply = client.messages.create(**kwargs)
            break
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
    if reply is None:
        assert last is not None
        run.error = f"{type(last).__name__} after {RETRIES} tries: {last}"[:500]
        _log(session, run)
        raise LLMError(f"the {agent} call failed ({run.error})") from last

    run.input_tokens = reply.usage.input_tokens
    run.output_tokens = reply.usage.output_tokens
    run.cache_read_tokens = getattr(reply.usage, "cache_read_input_tokens", None) or 0
    run.cache_write_tokens = getattr(reply.usage, "cache_creation_input_tokens", None) or 0
    run.cost_usd = cost_usd(model, reply.usage)
    if reply.stop_reason == "refusal":
        run.status, run.error = "refused", "the model declined this request"
        _log(session, run)
        raise LLMError(f"the {agent} declined to write this; change the source or the brief and try again")
    if reply.stop_reason == "max_tokens":
        run.error = f"reply cut off at {max_tokens} tokens"
        _log(session, run)
        raise LLMError(f"the {agent}'s reply was cut off at {max_tokens} tokens; the source is too long for one post")
    text = next((b.text for b in reply.content if b.type == "text"), "")
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
