"""Attente de stabilité : une page monopage déjà « complete » qui charge encore ses résultats."""
import time

from ely.chrome import ChromePage


class SpaBridge:
    """Page « complete » dont le contenu grossit pendant `growth` lectures, puis ne bouge plus."""

    def __init__(self, growth):
        self.growth = growth
        self.reads = []

    async def call(self, action, **kwargs):
        expr = kwargs['params']['expression']
        if 'readyState' in expr:
            return {'result': {'value': {'s': 'complete', 'u': 'https://mail.google.com/#search/x'}}}
        n = min(len(self.reads), self.growth)
        self.reads.append(n)
        return {'result': {'value': [100 + n, 10 * n]}}


async def test_networkidle_waits_until_results_are_rendered():
    bridge = SpaBridge(growth=6)
    await ChromePage(bridge, 7).wait_for_load_state('networkidle', timeout=10000)
    assert bridge.reads[-1] == 6
    assert bridge.reads.count(6) >= 4


async def test_networkidle_never_exceeds_timeout_on_busy_page():
    bridge = SpaBridge(growth=10 ** 6)
    start = time.monotonic()
    await ChromePage(bridge, 7).wait_for_load_state('networkidle', timeout=1500)
    assert time.monotonic() - start < 2.5
