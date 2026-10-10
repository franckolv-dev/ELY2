"""Le budget inclut les blocs rejoués ; un résumé vide ne peut effacer le contexte."""
import copy
import json

import pytest

from conftest import new_conversation
from ely.agent.loop import AgentLoop, load_history
from ely.agent.runner import runner
from ely.config import settings
from ely.db import db, now
from ely.llm.base import estimate_tokens
from ely.agent.runner import save_message


@pytest.mark.parametrize('block', [
    {'type': 'reasoning', 'encrypted_content': 'opaque' * 2000, 'summary': []},
    {'type': 'thinking', 'thinking': 'texte ' * 2000, 'signature': 'sig'},
    {'type': 'redacted_thinking', 'data': 'opaque' * 2000},
    {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'é😀' * 3000}]},
])
def test_estimate_counts_replayed_blocks_without_mutation(block):
    history = [{'role': 'assistant', 'content': '', 'thinking': [block]}]
    original = copy.deepcopy(history)
    assert estimate_tokens(history) == len(json.dumps([block], ensure_ascii=False)) // 3
    assert estimate_tokens(history) >= 2000
    assert history == original


@pytest.mark.parametrize('thinking', [None, []])
def test_empty_reasoning_preserves_existing_estimate(thinking):
    plain = [{'role': 'user', 'content': 'bonjour'},
             {'role': 'assistant', 'content': 'ok', 'tool_calls': [
                 {'id': 'x', 'name': 'test', 'arguments': {'value': 'y' * 90}}]}]
    augmented = copy.deepcopy(plain)
    augmented[1]['thinking'] = thinking
    assert estimate_tokens(augmented) == estimate_tokens(plain)


def test_reasoning_is_added_to_text_tools_and_images():
    m = {'role': 'assistant', 'content': [{'type': 'text', 'text': 'bonjour' * 10},
                                        {'type': 'image', 'data': 'fake'}],
         'images': [{'data': 'fake'}],
         'tool_calls': [{'id': 'x', 'arguments': {'text': 'sample' * 30}}]}
    before = estimate_tokens([m])
    m['thinking'] = [{'type': 'reasoning', 'encrypted_content': 'opaque' * 1000}]
    assert estimate_tokens([m]) == before + len(json.dumps(m['thinking'], ensure_ascii=False)) // 3


@pytest.fixture
def context_loop(fake, user, monkeypatch):
    monkeypatch.setattr(settings, 'context_soft_limit', 12000)
    cid = new_conversation(user)
    rid = db.insert('runs', conversation_id=cid, user_id=user['id'], status='running',
                    objective='audit synthétique', state='{}', created_at=now(), updated_at=now())
    loop = AgentLoop(runner, runner.state(cid, user['id']), rid, user)
    messages = [{'role': 'user', 'content': 'Effectuer un audit synthétique.'}]
    for i in range(24):
        messages.append({'role': 'assistant', 'content': '', 'model': 'fake:agent',
                         'thinking': [{'type': 'reasoning', 'encrypted_content': 'X' * 3000}],
                         'tool_calls': [
                             {'id': f'{i}-a', 'name': 'web_search', 'arguments': {'query': 'test'}},
                             {'id': f'{i}-b', 'name': 'web_fetch', 'arguments': {'url': 'https://example.org'}}]})
        messages.extend([{'role': 'tool', 'tool_call_id': f'{i}-a', 'name': 'web_search', 'content': 'preuve a'},
                         {'role': 'tool', 'tool_call_id': f'{i}-b', 'name': 'web_fetch', 'content': 'preuve b'}])
    for m in messages:
        m['_id'] = save_message(cid, rid, m, user['id'])
    return loop, messages


async def test_reasoning_triggers_automatic_compaction_and_preserves_tool_pairs(context_loop, fake):
    loop, history = context_loop
    before = copy.deepcopy(history)
    visible = [{k: v for k, v in m.items() if k != 'thinking'} for m in history]
    assert estimate_tokens(visible) < settings.context_soft_limit * 0.8
    assert estimate_tokens(history) > settings.context_soft_limit
    fake.script = lambda **kwargs: 'Résumé synthétique : continuer l’audit, preuves disponibles.'
    await loop.compact(history)  # surtout pas force=True : l'ancien calcul ne déclenchait rien
    assert len(history) < len(before)
    assert estimate_tokens(history) < settings.context_soft_limit
    assert history[0]['content'].startswith('[Résumé de la conversation')
    suffix = [m for m in before if m['_id'] > history[0]['_id']]
    assert history[1:] == suffix  # blocs récents, signatures et résultats intacts
    assert len(suffix) >= 3
    for i, m in enumerate(history):
        if m.get('tool_calls'):
            ids = [c['id'] for c in m['tool_calls']]
            assert [r['tool_call_id'] for r in history[i+1:i+1+len(ids)]] == ids
    assert load_history(loop.conv_id) == history
    assert db.val('SELECT COUNT(*) FROM messages WHERE run_id=?', (loop.run_id,)) == len(before)


@pytest.mark.parametrize('summary', ['', ' \n\t ', None])
async def test_empty_summary_does_not_advance_checkpoint_or_drop_history(context_loop, monkeypatch, summary):
    from ely.agent import loop as loop_module
    loop, history = context_loop
    before = copy.deepcopy(history)
    db.update('conversations', 'id=?', (loop.conv_id,), summary='résumé antérieur', summary_upto=0)

    async def empty(*args):
        return summary

    monkeypatch.setattr(loop_module, 'summarize', empty)
    await loop.compact(history, force=True)
    assert history == before
    row = db.one('SELECT summary,summary_upto FROM conversations WHERE id=?', (loop.conv_id,))
    assert row == {'summary': 'résumé antérieur', 'summary_upto': 0}


async def test_summary_transport_error_preserves_history(context_loop, monkeypatch):
    from ely.agent import loop as loop_module
    loop, history = context_loop
    before = copy.deepcopy(history)

    async def failed(*args):
        raise RuntimeError('transport indisponible')

    monkeypatch.setattr(loop_module, 'summarize', failed)
    await loop.compact(history, force=True)
    assert history == before
    assert not db.val('SELECT summary_upto FROM conversations WHERE id=?', (loop.conv_id,))
