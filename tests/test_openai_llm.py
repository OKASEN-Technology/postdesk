from types import SimpleNamespace

import pytest
from sqlalchemy import select

from postdesk import llm
from postdesk.config import settings
from postdesk.db.models import AgentRun

SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
          'required': ['ok'], 'additionalProperties': False}


class Client:
    def __init__(self, text='{"ok":true}', status='completed', output=None):
        self.responses = self
        self.calls = []
        self.reply = SimpleNamespace(status=status, output_text=text, output=output or [],
                                     usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                         input_tokens_details=SimpleNamespace(cached_tokens=200)))

    def create(self, **kw):
        self.calls.append(kw)
        return self.reply


def invoke(client, **kw):
    return llm.structured_call(agent='partner', system='instructions', schema=SCHEMA,
                               model='gpt-6.1-sol', effort='low', messages=kw.pop('messages', []),
                               client=client, **kw)


def test_openai_request_preserves_images_search_and_cost(session):
    search = SimpleNamespace(type='web_search_call')
    client = Client(output=[search])
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'hello'},
                llm.image_block(data=b'abc', media_type='image/png')]}]
    assert invoke(client, messages=messages, tools=[llm.web_search_tool(['example.com'], max_uses=2)],
                  session=session) == {'ok': True}
    request = client.calls[0]
    assert request['store'] is False and request['text']['format']['schema'] == SCHEMA
    assert request['input'][0]['content'][1]['image_url'] == 'data:image/png;base64,YWJj'
    assert request['tools'][0]['filters']['allowed_domains'] == ['example.com']
    assert request['max_tool_calls'] == 2
    run = session.execute(select(AgentRun)).scalar_one()
    assert run.status == 'ok' and float(run.cost_usd) == pytest.approx(.01262)


def test_openai_schema_and_incomplete_failures_are_logged(session):
    with pytest.raises(llm.LLMError, match='schema'):
        invoke(Client(text='{"wrong":true}'), session=session)
    with pytest.raises(llm.LLMError, match='did not complete'):
        invoke(Client(status='incomplete'), session=session)
    runs = session.execute(select(AgentRun).order_by(AgentRun.started_at)).scalars()
    assert [r.status for r in runs] == ['failed', 'failed']


def test_unknown_price_and_missing_key_fail_before_call(monkeypatch):
    client = Client()
    with pytest.raises(llm.LLMError, match='no configured price'):
        llm.structured_call(agent='maker', system='s', schema=SCHEMA, messages=[], model='gpt-unknown', client=client)
    assert not client.calls
    monkeypatch.setattr(settings, 'openai_api_key', None)
    with pytest.raises(llm.LLMError, match='OPENAI_API_KEY'):
        invoke(None)
