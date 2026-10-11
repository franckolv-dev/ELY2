"""Pagination explicite, bornée et en lecture seule de la recherche Gmail."""
import asyncio
from types import SimpleNamespace

import pytest

from ely.integrations import google
from ely.tools import TOOLS
from ely.tools import comms


@pytest.fixture
def gmail_pages(monkeypatch):
    calls = []
    state = {'pages': {
        '': {'messages': [{'id': f'm{i}'} for i in range(50)], 'nextPageToken': 'opaque-next'},
        'opaque-next': {'messages': [{'id': f'm{i}'} for i in range(50, 73)]},
    }, 'active': 0, 'peak': 0}

    async def api(uid, method, path, **kwargs):
        assert uid == 42 and method == 'GET'
        params = kwargs.get('params', {})
        calls.append((path, params.copy()))
        if path == f'{google.GMAIL}/messages':
            return state['pages'][params.get('pageToken', '')]
        state['active'] += 1
        state['peak'] = max(state['peak'], state['active'])
        try:
            await asyncio.sleep(0)
            return {'payload': {'headers': [{'name': 'Subject', 'value': 'Document synthétique'},
                                            {'name': 'From', 'value': 'test@example.org'},
                                            {'name': 'Date', 'value': '2026-01-01'}]},
                    'labelIds': ['UNREAD'], 'snippet': 'preuve synthétique'}
        finally:
            state['active'] -= 1

    monkeypatch.setattr(google, 'api', api)
    monkeypatch.setattr(comms, '_backend', lambda ctx: 'gmail')
    return SimpleNamespace(user_id=42), calls, state


async def test_two_pages_preserve_query_cursor_ids_and_bound_concurrency(gmail_pages):
    ctx, calls, state = gmail_pages
    first = await google.gmail_search_page(ctx.user_id, 'after:2026/01/01 before:2026/01/03', 50)
    assert len(first['messages']) == 50 and first['next_page_token'] == 'opaque-next'
    assert len([c for c in calls if c[0] == f'{google.GMAIL}/messages']) == 1  # pas de scan implicite
    second = await google.gmail_search_page(ctx.user_id, 'after:2026/01/01 before:2026/01/03', 50,
                                           first['next_page_token'])
    assert second['next_page_token'] == ''
    assert [m['id'] for m in first['messages'] + second['messages']] == [f'm{i}' for i in range(73)]
    lists = [params for path, params in calls if path == f'{google.GMAIL}/messages']
    assert lists == [
        {'q': 'after:2026/01/01 before:2026/01/03', 'maxResults': 50},
        {'q': 'after:2026/01/01 before:2026/01/03', 'maxResults': 50, 'pageToken': 'opaque-next'}]
    assert 1 < state['peak'] <= google.GMAIL_PARALLEL


async def test_legacy_search_still_returns_a_list(gmail_pages):
    ctx, calls, _ = gmail_pages
    result = await google.gmail_search(ctx.user_id, 'test', 50)
    assert isinstance(result, list) and len(result) == 50
    assert len(calls) == 51


async def test_tool_reports_partial_page_and_explicit_continuation(gmail_pages):
    ctx, calls, _ = gmail_pages
    first = await comms.email_search(ctx, query='in:inbox', limit=100, unread_only=True)
    assert not first.is_error
    assert '50 e-mail(s) sur cette page (limite effective : 50)' in first.content
    assert 'Résultats partiels' in first.content and 'next_page_token="opaque-next"' in first.content
    assert 'Fin des résultats' not in first.content
    second = await comms.email_search(ctx, query='in:inbox', limit=100, unread_only=True, page_token='opaque-next')
    assert '23 e-mail(s)' in second.content and 'Fin des résultats' in second.content
    assert '[m50]' in second.content and '[m0]' not in second.content
    lists = [p for path, p in calls if path == f'{google.GMAIL}/messages']
    assert lists[0]['q'] == lists[1]['q'] == 'in:inbox is:unread'
    assert lists[1]['pageToken'] == 'opaque-next'


@pytest.mark.parametrize('limit,effective', [(0, 1), (-5, 1), (1, 1), (15, 15), (50, 50), (1000, 50)])
async def test_tool_passes_effective_limit(gmail_pages, limit, effective):
    ctx, calls, state = gmail_pages
    state['pages'][''] = {}
    result = await comms.email_search(ctx, limit=limit)
    assert not result.is_error
    assert calls == [(f'{google.GMAIL}/messages', {'q': 'in:inbox', 'maxResults': effective})]


async def test_empty_intermediate_page_does_not_claim_completion(gmail_pages):
    ctx, calls, state = gmail_pages
    state['pages'][''] = {'nextPageToken': 'opaque-next'}
    result = await comms.email_search(ctx)
    assert 'Aucun e-mail sur cette page' in result.content
    assert 'Résultats partiels' in result.content and 'opaque-next' in result.content
    assert 'Fin des résultats' not in result.content
    assert len(calls) == 1


async def test_empty_final_page_is_explicit(gmail_pages):
    ctx, calls, state = gmail_pages
    state['pages']['opaque-next'] = {}
    result = await comms.email_search(ctx, page_token='opaque-next')
    assert 'Aucun e-mail sur cette page' in result.content and 'Fin des résultats' in result.content
    assert 'next_page_token' not in result.content and len(calls) == 1


async def test_invalid_cursor_propagates_instead_of_restarting(gmail_pages, monkeypatch):
    ctx, _, _ = gmail_pages
    requests = []

    async def fail(*args, **kwargs):
        requests.append(kwargs['params'])
        raise RuntimeError('invalid page token')

    monkeypatch.setattr(google, 'api', fail)
    with pytest.raises(RuntimeError, match='invalid page token'):
        await comms.email_search(ctx, query='test', page_token='invalid')
    assert requests == [{'q': 'test', 'maxResults': 15, 'pageToken': 'invalid'}]


@pytest.mark.parametrize('token', ['', 'opaque-next'])
async def test_imap_is_explicit_about_pagination_and_coverage(monkeypatch, token):
    monkeypatch.setattr(comms, '_backend', lambda ctx: 'imap')
    calls = []

    async def search(*args):
        calls.append(args)
        return []

    monkeypatch.setattr(comms.mail, 'search', search)
    result = await comms.email_search(SimpleNamespace(user_id=42), query='test', folder='Archive', page_token=token)
    if token:
        assert result.is_error and 'Gmail' in result.content and calls == []
    else:
        assert not result.is_error and 'exhaustivité non vérifiée' in result.content
        assert calls == [(42, 'test', 15, False, 'Archive')]


def test_page_token_is_exposed_in_tool_schema():
    assert TOOLS['email_search'].parameters['properties']['page_token']['type'] == 'string'
