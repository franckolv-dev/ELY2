"""Météo : un lieu précisé « ville, département, pays » est trouvé, et la précision départage les homonymes."""
from __future__ import annotations

import httpx
from conftest import new_conversation

from ely.tools import ToolContext, execute, web


async def _noop(*a, **k):
    pass


async def test_weather_accepts_city_with_region_and_country(user, monkeypatch):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in request.url.host:
            asked.append(request.url.params["name"])
            if request.url.params["name"] != "Bédarieux":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"results": [
                {"name": "Bédarieux", "latitude": 1.0, "longitude": 1.0, "admin1": "Québec", "country": "Canada"},
                {"name": "Bédarieux", "latitude": 43.6, "longitude": 3.2, "admin1": "Occitanie", "admin2": "Hérault", "country": "France"}]})
        assert request.url.params["latitude"] == "43.6"
        return httpx.Response(200, json={
            "current": {"temperature_2m": 18, "apparent_temperature": 17, "weather_code": 0, "wind_speed_10m": 5, "relative_humidity_2m": 60},
            "daily": {"time": ["2026-10-19"], "weather_code": [0], "temperature_2m_min": [9], "temperature_2m_max": [20],
                      "precipitation_sum": [0], "precipitation_probability_max": [0], "wind_speed_10m_max": [12]}})

    real = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    r = await execute(ctx, "weather", {"location": "Bédarieux, Hérault, France"})
    assert not r.is_error, r.content
    assert asked == ["Bédarieux"] and "Occitanie" in r.content and "France" in r.content
