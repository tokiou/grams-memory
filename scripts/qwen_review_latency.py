#!/usr/bin/env python3
"""Measure direct OpenRouter latency for a simulated Supervisor tool decision."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from typing import Any

import httpx


SYSTEM_PROMPT = """You are a memory supervisor reviewing an agent tool event.
Decide exactly one action and return only valid JSON:
{"action":"MEMORY_OPERATION|INTERVENE|DONE","reason":"short explanation"}

Use MEMORY_OPERATION only when the event contains durable knowledge worth saving.
Use INTERVENE only when the agent is stuck or needs corrective guidance.
Use DONE when no supervisor action is needed.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--requests", type=int, default=5, help="number of identical requests (default: 5)")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-request timeout in seconds (default: 120)")
    parser.add_argument("--tool", default="bash", help="simulated tool name (default: bash)")
    return parser.parse_args()


def extract_decision(body: dict[str, Any]) -> dict[str, Any]:
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = str(content).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    decision = json.loads(text)
    if not isinstance(decision, dict):
        raise ValueError("model response was not a JSON object")
    return decision


def main() -> int:
    args = parse_args()
    if args.requests < 1:
        raise SystemExit("--requests must be at least 1")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required; load .env or export it first")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    model = os.getenv("OPENROUTER_DEPLOYMENT", os.getenv("GRAMS_SUPERVISOR_MODEL", "deepseek/deepseek-v4-flash-0731"))
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_tool": args.tool,
                        "tool_status": "completed",
                        "tool_command": "git cherry-pick 650dba4",
                        "tool_output_summary": "Cherry-pick completed successfully and restored the user's lost changes.",
                        "task": "Recover the lost git changes and merge them into master.",
                        "pending_events": 0,
                    }
                ),
            },
        ],
    }

    print(f"endpoint={base_url}/chat/completions")
    print(f"model={model} requests={args.requests} timeout_s={args.timeout}")
    durations: list[float] = []
    with httpx.Client(timeout=args.timeout) as client:
        for index in range(1, args.requests + 1):
            started = time.perf_counter()
            try:
                response = client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                decision = extract_decision(response.json())
            except Exception as error:
                duration_ms = (time.perf_counter() - started) * 1000
                print(f"request={index} duration_ms={duration_ms:.1f} error={type(error).__name__}: {error}")
                return 1
            duration_ms = (time.perf_counter() - started) * 1000
            durations.append(duration_ms)
            print(
                f"request={index} duration_ms={duration_ms:.1f} "
                f"action={decision.get('action', '<missing>')} "
                f"reason={json.dumps(str(decision.get('reason', '')), ensure_ascii=True)}"
            )

    print(
        "summary_ms="
        + json.dumps(
            {
                "min": round(min(durations), 1),
                "mean": round(statistics.mean(durations), 1),
                "median": round(statistics.median(durations), 1),
                "max": round(max(durations), 1),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
