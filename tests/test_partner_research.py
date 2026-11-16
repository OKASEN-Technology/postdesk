import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

from postdesk.agents.partner import research
from postdesk.config import config


def test_search_uses_bounded_argument_list(monkeypatch):
    def run(args, **kwargs):
        assert args == ['agent-reach', 'web', 'search', 'physical AI', '--limit', '5']
        assert kwargs['timeout'] == 20
        assert 'shell' not in kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'items': [{'url': 'https://example.com'}]}))
    monkeypatch.setattr(subprocess, 'run', run)
    assert research._run('search', 'physical AI')[0][0]['url'] == 'https://example.com'


def test_unavailable_search_is_explicit(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, 'run', missing)
    assert research._run('search', 'robotics')[1] == 'Agent Reach is not installed'


def test_collect_filters_stale_deduplicates_and_bounds_reads(monkeypatch):
    monkeypatch.setattr(config.research, 'topics', ['robotics'])
    monkeypatch.setattr(config.research, 'max_reads', 1)
    calls = []
    def run(action, value, **kwargs):
        calls.append(action)
        if action == 'read':
            return [{'text': 'Full source content'}], None
        return [dict(url='https://example.com/new', title='New', published_at='2026-11-15'),
                dict(url='https://example.com/new', title='Duplicate'),
                dict(url='https://example.com/old', published_at='2020-01-01'),
                dict(url='javascript:alert(1)')], None
    monkeypatch.setattr(research, '_run', run)
    result = research.collect(now=datetime(2026, 11, 16, tzinfo=UTC))
    assert result['status'] == 'ok'
    assert len(result['items']) == 1
    assert result['items'][0]['excerpt'] == 'Full source content'
    assert calls == ['search', 'read']


def test_collect_exposes_failure_without_fabricating_sources(monkeypatch):
    monkeypatch.setattr(config.research, 'topics', ['robotics'])
    monkeypatch.setattr(research, '_run', lambda *a, **k: ([], 'Research timed out'))
    result = research.collect()
    assert result['status'] == 'failed'
    assert result['items'] == []
    assert result['error'] == 'Research timed out'


def test_daily_partner_keeps_research_on_same_card_and_waits_for_choice(session, monkeypatch):
    from postdesk.agents import partner
    from postdesk.board import cards
    from tests import boardkit

    findings = {'status': 'ok', 'items': [{'title': 'Research report', 'url': 'https://example.com/2026/report',
                                           'excerpt': 'The paper discusses safer robotics.', 'read': True}],
                'topics': ['robotics'], 'error': None}
    calls = []
    monkeypatch.setattr(research, 'collect', lambda: calls.append('research') or findings)
    card = cards.file_card(session, title='Daily idea', created_by='daily-ideas', column='Planned',
                           source_ref='daily-idea:2026-11-16', fingerprint='daily-idea:2026-11-16')
    reply = boardkit.partner_reply(brief='A potential angle with a linked source.', guidance='Lead with safety.')
    model = boardkit.scripted(reply)
    partner.turn(session, card, call=model)
    assert card.column == 'Planned'
    assert card.plan['daily_research'] == findings
    assert card.facts['citations'][0]['url'] == 'https://example.com/2026/report'
    assert 'unverified research leads' in card.facts['body_md']
    assert 'untrusted data' in model.calls[0]['messages'][0]['content'][1]['text']
    partner.turn(session, card, call=model)
    assert card.column == 'Planned'
    assert calls == ['research']


def test_url_digits_do_not_trigger_number_guard():
    from postdesk.agents.partner import check_brief
    assert check_brief({'brief': 'Source: https://example.com/report/2026', 'guidance': ''}, set()) == []
    assert check_brief({'brief': 'The report covers 2026 workers.', 'guidance': ''}, set())
