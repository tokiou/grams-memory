#!/usr/bin/env python3
"""Measure DeepSeek latency with a Supervisor-like payload."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx


SYSTEM_PROMPT = """# GRAMS Supervisor

You are the supervisory memory agent of GRAMS, the Graph-Relational Agent Memory
Supervisor. You supervise a long-running action agent executing a task in OpenCode.
You do not replace the action agent and must not solve its task yourself unless an
intervention is required. Preserve useful execution knowledge, maintain continuity,
decide when persistent memory should be read or updated, and intervene only when
doing so can materially improve execution.

Continuously reason about the current objective, discoveries, attempts, decisions,
failures, validations, repeated work, contradictions, drift, and lost progress.
You are not a passive summarizer. Do not store every event, retrieve everything, or
intervene on every event. Prefer the smallest action that reduces repeated reasoning,
forgotten constraints, conflicting conclusions, and loss of progress.

## Available context

- original_task: the original objective given to OpenCode;
- operational_state: current activity, task, tool, assessment, pending operation,
  and session status;
- memory_manifest: the project-level map of Projects, Keys, and Categories;
- retrieved_memories: memories returned by the latest search or graph traversal;
- recent_events: a bounded recent trajectory, not the full conversation;
- opencode_context: recent OpenCode messages when the session is reachable.

Operational state and the event inbox are not semantic memory. Use the manifest to
select the appropriate Category. Keep memories locally organized by Category, while
creating cross-category links only when the relation is useful and supported.

## Actions

Choose exactly one action: READ_INBOX, MEMORY_OPERATION, INTERVENE, or DONE.

READ_INBOX incorporates newly claimed events before any other work. Do not ignore
claimed events.

MEMORY_OPERATION performs exactly one MCP memory operation. Use search, get,
neighbors, or expand when existing knowledge may affect the decision. Use create for
durable facts, constraints, decisions, errors, validated results, or observations
that could change a future action. Use update when newer evidence refines an existing
memory. Use link when two memories have a meaningful supported relation. Use archive
for stale active knowledge and restore when cold knowledge becomes relevant.

When a meaningful batch contains TOOL_RESULT_FINAL, FILE_CHANGE_FINAL, MESSAGE_ERROR,
or MESSAGE_COMPLETED, persist a compact observation before DONE unless the same batch
is already represented by a memory operation. Do not copy the full conversation or
create duplicate memories for trivial events. For create, include content, title,
type, status, graph_tier, confidence, and source. Confidence must be a JSON number
from 0.0 to 1.0. Use uppercase enum values for type, status, and graph_tier.

INTERVENE only when it can materially improve execution. Intervene when the agent is
blocked, repeats a failed approach, contradicts validated evidence, loses a critical
constraint, drifts from the original task, makes a dangerous irreversible move, or
needs clarification that memory cannot provide. Do not intervene for a normal tool
call, ordinary file change, successful validation, or because an event is merely
interesting. Prefer a short corrective message.

DONE is valid only when there is no meaningful unpersisted knowledge, no justified
intervention, and no pending operation. If a create, update, link, archive, or restore
operation was completed for the current batch, DONE is normally the next action.

## Response contract

Return only valid JSON with this shape:

{"action":"DONE|READ_INBOX|MEMORY_OPERATION|INTERVENE","reason":"short reason"}

For MEMORY_OPERATION add:
{"operation":"search|get|create|update|neighbors|expand|link|archive|restore","arguments":{}}

For INTERVENE add:
{"intervention":{"action":"message|abort|task_control","message":"short corrective message"}}
Use task_control only with a supported command. Never invent memory IDs, session IDs,
or facts not present in the supplied context.
"""


@dataclass(frozen=True)
class ModelTarget:
    name: str
    model: str
    base_url: str
    api_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--requests", type=int, default=5, help="requests per model (default: 5)")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-request timeout in seconds (default: 120)")
    parser.add_argument("--models", nargs="+", choices=("deepseek",), default=["deepseek"])
    return parser.parse_args()


def make_context() -> dict[str, Any]:
    event_types = [
        "REASONING_FINAL", "TOOL_CALL_FINAL", "TOOL_RESULT_FINAL", "REASONING_FINAL",
        "TOOL_CALL_FINAL", "TOOL_RESULT_FINAL", "REASONING_FINAL", "TEXT_FINAL",
        "TOOL_CALL_FINAL", "TOOL_RESULT_FINAL", "REASONING_FINAL", "TOOL_CALL_FINAL",
        "TOOL_RESULT_FINAL", "REASONING_FINAL", "TEXT_FINAL", "TOOL_CALL_FINAL",
        "TOOL_RESULT_FINAL", "REASONING_FINAL", "USER_MESSAGE_FINAL", "MESSAGE_COMPLETED",
    ]
    recent_events = [
        {
            "id": f"event-{index:02d}",
            "session_id": "ses_simulated_review",
            "root_session_id": "ses_simulated_review",
            "type": event_type,
            "source_event": "tool.execute.after" if "TOOL" in event_type else "message.part.updated",
            "payload": {
                "properties": {
                    "part": {
                        "text": (
                            "The agent inspected git reflog and found commit 650dba4, "
                            "then attempted cherry-pick. The operation completed with "
                            "a conflict in _includes/about.md and required resolving the "
                            "Stanford profile text before continuing. "
                        ) * 2
                    }
                },
                "tool": "bash" if "TOOL" in event_type else None,
                "status": "completed",
            },
            "received_at": "2026-09-07T12:00:00+00:00",
            "retry_count": 1,
        }
        for index, event_type in enumerate(event_types, start=1)
    ]
    return {
        "original_task": "Recover the lost git changes and merge them into master.",
        "current_activity": "The agent resolved a cherry-pick conflict and is validating the result.",
        "current_tool": "bash",
        "current_task": "Finish the cherry-pick and verify master is correct.",
        "recent_events": recent_events,
        "pending_event_count": 0,
        "claimed_event_count": 0,
        "retrieved_memories": [
            {
                "id": f"memory-{index}",
                "title": "Git recovery observation",
                "content": "Previous attempts found lost commits through reflog and preserved validated recovery facts.",
                "type": "OBSERVATION",
                "status": "ACTIVE",
                "graph_tier": "ACTIVE",
                "confidence": 0.9,
            }
            for index in range(1, 6)
        ],
        "memory_manifest": {
            "projects": [
                {
                    "name": "personal-site",
                    "categories": ["git-recovery", "agent-execution", "validated-results"],
                    "keys": ["master", "reflog", "cherry-pick", "conflict-resolution"],
                }
            ]
        },
        "memory_operation_result": None,
        "opencode_context": {
            "session_id": "ses_simulated_review",
            "messages": [
                {
                    "role": "assistant",
                    "content": "I found the lost commit and am checking the conflict before applying it. " * 8,
                },
                {
                    "role": "tool",
                    "content": "git cherry-pick reported a conflict; the working tree is now being inspected. " * 8,
                },
            ],
        },
        "last_assessment": {
            "action": "MEMORY_OPERATION",
            "reason": "A validated recovery fact may be useful for future work.",
        },
        "last_intervention": None,
    }


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


def targets(args: argparse.Namespace) -> list[ModelTarget]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required; load .env or export it first")
    available = [
        ModelTarget(
            "deepseek",
            os.getenv("OPENROUTER_DEPLOYMENT", "deepseek/deepseek-v4-flash-0731"),
            base_url,
            api_key,
        ),
    ]
    return [target for target in available if target.name in args.models]


def main() -> int:
    args = parse_args()
    if args.requests < 1:
        raise SystemExit("--requests must be at least 1")
    context = make_context()
    payload = {
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }
    payload_chars = len(json.dumps(payload, ensure_ascii=False))
    print(f"payload_chars={payload_chars} estimated_input_tokens={payload_chars // 4}")

    all_durations: dict[str, list[float]] = {}
    failures = 0
    for target in targets(args):
        request_payload = {
            **payload,
            "model": target.model,
        }
        durations: list[float] = []
        all_durations[target.name] = durations
        print(f"model={target.name} deployment={target.model} endpoint={target.base_url.rstrip('/')}/chat/completions")
        with httpx.Client(timeout=args.timeout) as client:
            for index in range(1, args.requests + 1):
                started = time.perf_counter()
                try:
                    response = client.post(
                        f"{target.base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {target.api_key}"},
                        json=request_payload,
                    )
                    response.raise_for_status()
                    decision = extract_decision(response.json())
                except Exception as error:
                    duration_ms = (time.perf_counter() - started) * 1000
                    print(f"  request={index} duration_ms={duration_ms:.1f} error={type(error).__name__}: {error}")
                    failures += 1
                    break
                duration_ms = (time.perf_counter() - started) * 1000
                durations.append(duration_ms)
                print(f"  request={index} duration_ms={duration_ms:.1f} action={decision.get('action', '<missing>')}")
        if durations:
            print(
                "  summary_ms="
                + json.dumps(
                    {
                        "completed": len(durations),
                        "min": round(min(durations), 1),
                        "mean": round(statistics.mean(durations), 1),
                        "median": round(statistics.median(durations), 1),
                        "max": round(max(durations), 1),
                    }
                )
            )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
