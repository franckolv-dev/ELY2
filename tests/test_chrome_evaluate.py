"""Runtime.evaluate : fonctions et expressions exécutées exactement une fois.

Exécute le JavaScript émis dans V8 (Node), sans navigateur ni compte réel.
"""
import asyncio
import json
import shutil

import pytest

from ely.chrome import ChromeError, ChromePage

NODE = shutil.which('node')
RUNNER = r"""
const vm = require('node:vm');
let input = '';
process.stdin.on('data', b => input += b);
process.stdin.on('end', async () => {
  const ctx = { calls: 0 };
  try {
    const value = await vm.runInNewContext(JSON.parse(input).expression, ctx, { timeout: 1000 });
    console.log(JSON.stringify({ result: { value }, calls: ctx.calls }));
  } catch (e) {
    console.log(JSON.stringify({ exceptionDetails: { exception: { description: String(e) } }, calls: ctx.calls }));
  }
});
"""


class V8Bridge:
    def __init__(self):
        self.requests = []
        self.calls = None

    async def call(self, action, **kwargs):
        self.requests.append((action, kwargs))
        assert action == 'cdp'
        assert kwargs['tab_id'] == 7
        assert kwargs['method'] == 'Runtime.evaluate'
        params = kwargs['params']
        assert params['returnByValue'] is True
        assert params['awaitPromise'] is True
        assert params['userGesture'] is True
        proc = await asyncio.create_subprocess_exec(
            NODE, '-e', RUNNER, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(params).encode()), 5)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        assert proc.returncode == 0, err.decode()
        result = json.loads(out)
        self.calls = result.pop('calls')
        return result


@pytest.fixture
def bridge():
    if not NODE:
        pytest.skip('Node/V8 absent : tests JavaScript locaux')
    return V8Bridge()


@pytest.mark.parametrize('source,expected', [
    ('(() => { calls++; return 42; })()', 42),
    ('(() => { calls++; return {html: "preuve"}; })()', {'html': 'preuve'}),
    ('(() => { calls++; return [1, 2]; })()', [1, 2]),
    ('(() => { calls++; return null; })()', None),
    ('(() => { calls++; })()', None),
    ('(() => { calls++; return false; })()', False),
    ('(() => { calls++; return ""; })()', ''),
    ('(() => { calls++; return Promise.resolve(42); })()', 42),
    ('(async () => { calls++; return await Promise.resolve(42); })()', 42),
    ('(function () { calls++; return 42; })()', 42),
    ('(() => { calls++; return 42; })();\n', 42),
    ('() => { calls++; return 42; }', 42),
    ('async () => { calls++; return await Promise.resolve(42); }', 42),
    # sans espace après async (routine matinale : la fonction n'était pas appelée, résultat {})
    ('async()=>{ calls++; return {total: 2}; }', {'total': 2}),
    ('async(x)=>{ calls++; return 42; }', 42),
    ('function () { calls++; return 42; }', 42),
    ('arg => { calls++; return 42; }', 42),
    ('calls++; 42', 42),
    ('[1, 2].map(x => { calls++; return x * 2; })', [2, 4]),
])
async def test_evaluate_functions_and_expressions_once(bridge, source, expected):
    page = ChromePage(bridge, 7)
    assert await page.evaluate(source) == expected
    assert len(bridge.requests) == 1
    assert bridge.calls == (2 if source.startswith('[1, 2]') else 1)


@pytest.mark.parametrize('arg', [None, False, {'text': 'é"\\\n'}, [1, 2]])
async def test_evaluate_passes_json_arguments(bridge, arg):
    page = ChromePage(bridge, 7)
    assert await page.evaluate('(value) => { calls++; return value; }', arg) == arg
    assert bridge.calls == 1 and len(bridge.requests) == 1


async def test_no_argument_is_not_null(bridge):
    page = ChromePage(bridge, 7)
    assert await page.evaluate('function () { return arguments.length; }') == 0
    assert await page.evaluate('function () { return arguments.length; }', None) == 1


@pytest.mark.parametrize('source', [
    '(() => { calls++; throw new Error("échec réel"); })()',
    '() => { calls++; throw new Error("échec réel"); }',
    'async () => { calls++; throw new Error("échec réel"); }',
])
async def test_javascript_failure_is_reported_without_replay(bridge, source):
    page = ChromePage(bridge, 7)
    with pytest.raises(ChromeError, match='échec réel'):
        await page.evaluate(source)
    assert len(bridge.requests) == 1 and bridge.calls == 1
