import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

sys.path.insert(0, str(Path(__file__).parents[1] / "supervisor"))

from supervisor.agent.nodes.review import OpenAIReviewModel


def test_review_request_lets_openrouter_choose_provider():
    async def scenario():
        model = OpenAIReviewModel(
            "https://openrouter.ai/api/v1",
            "test-key",
            "deepseek/deepseek-v4-flash-0731",
        )
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"action":"DONE","reason":"ok"}'}},
                ],
            },
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
        )
        model._http.post = AsyncMock(return_value=response)
        try:
            decision = await model.decide({"events": []})
            request = model._http.post.call_args.kwargs["json"]
        finally:
            await model.aclose()

        assert decision["action"] == "DONE"
        assert request["model"] == "deepseek/deepseek-v4-flash-0731"
        assert "provider" not in request
        assert request["reasoning"] == {"effort": "none"}
        assert "max_tokens" not in request
        assert request["response_format"] == {"type": "json_object"}
        assert 'Return only JSON: {"action": "...", "reason": "..."}.' not in request["messages"][0]["content"]
        assert "matching the response contract above" in request["messages"][0]["content"]

    import asyncio

    asyncio.run(scenario())
