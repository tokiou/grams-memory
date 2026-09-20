import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.opencode.client import OpenCodeClient


def test_abort_then_prompt_uses_the_same_session_and_accepts_204():
    async def scenario():
        calls = []

        async def handler(request):
            calls.append((request.method, request.url.raw_path.decode(), request.content))
            return httpx.Response(204)

        client = OpenCodeClient("http://opencode")
        original_http = client._http
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await original_http.aclose()
        try:
            assert await client.abort_session("session/one") is None
            assert await client.send_message("session/one", "Implement now.") is None
        finally:
            await client.aclose()

        assert calls == [
            ("POST", "/session/session%2Fone/abort", b""),
            (
                "POST",
                "/session/session%2Fone/prompt_async",
                b'{"parts":[{"type":"text","text":"Implement now."}]}',
            ),
        ]

    asyncio.run(scenario())
