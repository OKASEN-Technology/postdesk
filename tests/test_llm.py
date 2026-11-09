"""postdesk.llm: one structured call, its retries, its failure modes and
the run log, with the Anthropic client faked."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from anthropic import BadRequestError, RateLimitError
from sqlalchemy import select

from postdesk import llm
from postdesk.config import config, settings
from postdesk.db.models import AgentRun

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}


def _reply(text='{"ok": true}', stop="end_turn", inp=1000, out=200, read=0, write=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read,
                              cache_creation_input_tokens=write),
    )


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def _api_error(cls, status, message):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=req), body=None)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")


def test_the_call_shape_and_the_run_log(session):
    fake = FakeClient(_reply(read=800, write=100))
    out = llm.structured_call(agent="maker", system="Be brief.", messages=[{"role": "user", "content": "hi"}],
                              schema=SCHEMA, session=session, ref="md:a.md", client=fake)
    assert out == {"ok": True}
    kw = fake.calls[0]
    assert kw["model"] == config.llm.maker.model and kw["max_tokens"] == llm.MAX_TOKENS
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["system"][0]["text"] == "Be brief."
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA},
                                   "effort": config.llm.maker.effort}
    assert "tool_choice" not in kw and "tools" not in kw
    run = session.execute(select(AgentRun)).scalar_one()
    assert run.agent == "maker" and run.status == "ok" and run.ref == "md:a.md" and run.error is None
    assert (run.input_tokens, run.output_tokens, run.cache_read_tokens, run.cache_write_tokens) == (1000, 200, 800, 100)
    # sonnet 5.5: 1000 in at $2, 200 out at $10, 800 cache reads at $0.20, 100 cache writes at $2.50, per MTok
    assert float(run.cost_usd) == pytest.approx((1000 * 2 + 200 * 10 + 800 * 0.2 + 100 * 2.5) / 1e6)
    assert run.finished_at is not None and run.started_at <= run.finished_at


def test_prices_match_by_prefix_and_unknown_models_cost_none():
    assert llm.price_of("claude-sonnet-5-5-20260301") == llm.PRICES["claude-sonnet-5-5"]
    assert llm.price_of("claude-sonnet-5") == llm.PRICES["claude-sonnet-5"]
    assert llm.price_of("claude-something-new") is None
    assert llm.cost_usd("claude-something-new", SimpleNamespace(input_tokens=1, output_tokens=1)) is None


def test_transient_errors_are_retried_and_others_are_not(session):
    fake = FakeClient(_api_error(RateLimitError, 429, "slow down"), _reply())
    assert llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA, session=session, client=fake)
    assert len(fake.calls) == 2

    bad = FakeClient(_api_error(BadRequestError, 400, "Your credit balance is too low"))
    with pytest.raises(llm.LLMError, match="credit balance is too low"):
        llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA, session=session, client=bad)
    assert len(bad.calls) == 1
    runs = session.execute(select(AgentRun).order_by(AgentRun.started_at)).scalars().all()
    assert [r.status for r in runs] == ["ok", "failed"] and "HTTP 400" in runs[1].error

    tired = FakeClient(*[_api_error(RateLimitError, 429, "slow down")] * llm.RETRIES)
    with pytest.raises(llm.LLMError, match=f"after {llm.RETRIES} tries"):
        llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA, session=session, client=tired)
    assert len(tired.calls) == llm.RETRIES


def test_a_refusal_or_a_cut_off_reply_is_a_failed_turn_not_a_retry(session):
    fake = FakeClient(_reply(text="", stop="refusal"))
    with pytest.raises(llm.LLMError, match="declined"):
        llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA, session=session, client=fake)
    assert len(fake.calls) == 1
    cut = FakeClient(_reply(text='{"ok": tr', stop="max_tokens"))
    with pytest.raises(llm.LLMError, match="cut off"):
        llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA, session=session, client=cut)
    runs = session.execute(select(AgentRun).order_by(AgentRun.started_at)).scalars().all()
    assert [r.status for r in runs] == ["refused", "failed"]
    assert runs[0].cost_usd is not None  # a refusal still cost its tokens


def test_the_monthly_ceiling_stops_the_agents(session, monkeypatch):
    monkeypatch.setattr(config.llm, "monthly_usd", 1.0)
    now = datetime.now(UTC)
    session.add(AgentRun(agent="maker", model="claude-sonnet-5-5", cost_usd=0.6, started_at=now, status="ok"))
    session.add(AgentRun(agent="editor", model="claude-opus-5-5", cost_usd=0.5, started_at=now, status="ok"))
    session.commit()
    assert llm.month_spend(session) == pytest.approx(1.1)
    fake = FakeClient(_reply())
    with pytest.raises(llm.BudgetExceeded, match=r"\$1.10, over the \$1.00 ceiling"):
        llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA, session=session, client=fake)
    assert fake.calls == []


def test_no_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.structured_call(agent="maker", system="s", messages=[], schema=SCHEMA)


def test_model_and_effort_come_from_config(monkeypatch):
    assert llm.model_for("maker") == (config.llm.maker.model, config.llm.maker.effort)
    assert llm.model_for("partner") == (config.llm.partner.model, config.llm.partner.effort)
    assert llm.model_for("something-else") == llm.model_for("maker")
