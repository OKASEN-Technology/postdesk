"""OpenAI Responses adapter for the existing Postdesk agent contracts."""
from __future__ import annotations

import json
import time

import jsonschema
import openai
from openai import OpenAI

from postdesk import llm
from postdesk.config import settings


def _input(messages):
    result = []
    for message in messages:
        role = message['role']
        content = message['content']
        if isinstance(content, str):
            result.append({'role': role, 'content': content})
            continue
        blocks = []
        for block in content:
            if block['type'] == 'text':
                blocks.append({'type': 'output_text' if role == 'assistant' else 'input_text', 'text': block['text']})
            elif block['type'] == 'image' and role == 'user':
                source = block['source']
                url = (source['url'] if source['type'] == 'url'
                       else f"data:{source['media_type']};base64,{source['data']}")
                blocks.append({'type': 'input_image', 'image_url': url})
            else:
                raise llm.LLMError(f"Unsupported OpenAI message block: {block['type']}")
        result.append({'role': role, 'content': blocks})
    return result


def structured_openai(*, agent, system, messages, schema, model, effort, max_tokens, tools, session, run, client=None):
    rates = llm.OPENAI_PRICES.get(model)
    if not rates:
        raise llm.LLMError(f"OpenAI model {model!r} has no configured price; select a listed model "
                           "to enforce the budget")
    if client is None:
        if not settings.openai_api_key:
            raise llm.LLMError('OPENAI_API_KEY is not configured; add it to the server environment')
        client = OpenAI(api_key=settings.openai_api_key, max_retries=0)
    kwargs = {
        'model': model, 'instructions': system, 'input': _input(messages), 'max_output_tokens': max_tokens,
        'reasoning': {'effort': effort}, 'store': False,
        'text': {'format': {'type': 'json_schema', 'name': 'postdesk_agent', 'schema': schema, 'strict': False}},
    }
    if tools:
        if len(tools) != 1 or tools[0].get('type') != llm.WEB_SEARCH_TOOL or not tools[0].get('allowed_domains'):
            raise llm.LLMError('Unsupported OpenAI tool configuration')
        kwargs['tools'] = [{'type': 'web_search', 'filters': {'allowed_domains': tools[0]['allowed_domains']}}]
        kwargs['max_tool_calls'] = tools[0]['max_uses']
    last = None
    for attempt in range(llm.RETRIES):
        try:
            reply = client.responses.create(**kwargs)
            break
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            last = e
            if attempt < llm.RETRIES - 1:
                time.sleep(2 ** attempt)
        except openai.APIError as e:
            run.error = f'{type(e).__name__}: {e}'[:500]
            llm._log(session, run)
            raise llm.LLMError(f'the {agent} call failed: {run.error}') from e
    else:
        run.error = f'{type(last).__name__} after {llm.RETRIES} tries'
        llm._log(session, run)
        raise llm.LLMTransient(f'the {agent} call failed: {run.error}') from last
    usage = reply.usage
    if usage:
        run.input_tokens = usage.input_tokens
        run.output_tokens = usage.output_tokens
        run.cache_read_tokens = getattr(getattr(usage, 'input_tokens_details', None), 'cached_tokens', 0) or 0
        searches = sum(1 for item in reply.output if getattr(item, 'type', None) == 'web_search_call')
        inp, out, cached = rates
        run.cost_usd = round(((run.input_tokens - run.cache_read_tokens) * inp + run.cache_read_tokens * cached
                              + run.output_tokens * out) / 1_000_000 + searches * llm.WEB_SEARCH_USD, 6)
    else:
        run.error = 'OpenAI response did not include usage; cost cannot be tracked'
        llm._log(session, run)
        raise llm.LLMError(run.error)
    refusal = any(getattr(block, 'type', None) == 'refusal' for item in reply.output
                  for block in (getattr(item, 'content', None) or []))
    if refusal:
        run.status, run.error = 'refused', 'the model declined this request'
        llm._log(session, run)
        raise llm.LLMError(f'the {agent} declined this request')
    if reply.status != 'completed':
        run.error = f'OpenAI response did not complete: {reply.status}'
        llm._log(session, run)
        raise llm.LLMError(run.error)
    try:
        result = json.loads(reply.output_text)
        jsonschema.validate(result, schema)
        if not isinstance(result, dict):
            raise ValueError('response is not an object')
    except (ValueError, jsonschema.ValidationError) as e:
        run.error = 'OpenAI response did not match the agent schema'
        llm._log(session, run)
        raise llm.LLMError(run.error) from e
    run.status = 'ok'
    llm._log(session, run)
    return result
