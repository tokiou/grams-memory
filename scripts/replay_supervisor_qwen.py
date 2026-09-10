#!/usr/bin/env python3
"""Replay an ATIF trajectory through the Supervisor with real OpenRouter decisions.

Harbor and OpenCode are never started. OpenCode and memory calls are recorded
by local doubles, so this script is safe for controlled experiments.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[1]
# The Python package lives one level below the application directory.
sys.path.insert(0, str(ROOT / "grams-app" / "supervisor"))

from supervisor.agent.nodes.intervene import make_intervene_node  # noqa: E402
from supervisor.agent.nodes.memory_operation import make_memory_operation_node  # noqa: E402
from supervisor.agent.nodes.read_inbox import make_read_inbox_node  # noqa: E402
from supervisor.agent.nodes.review import OpenAIReviewModel, ReviewModel, ReviewService, make_review_node  # noqa: E402
from supervisor.agent.state import SupervisorState  # noqa: E402
from supervisor.memory.manifest import categories  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="ATIF trajectory.json")
    parser.add_argument("--batch-window", type=float, default=2.0, help="group events within this many seconds")
    parser.add_argument("--timeout", type=float, default=60.0, help="OpenRouter request timeout in seconds")
    parser.add_argument("--memory-mode", choices=("empty", "seeded"), default="seeded", help="simulated memory contents")
    parser.add_argument("--max-events", type=int, help="replay only the first N converted events")
    parser.add_argument("--max-steps", type=int, default=30, help="maximum graph steps per batch (default: 30)")
    parser.add_argument("--output", type=Path, help="write the report to this JSON file")
    return parser.parse_args()


def validate_configuration(args: argparse.Namespace) -> None:
    missing = [name for name in ("OPENROUTER_DEPLOYMENT", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL") if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(f"real OpenRouter replay requires: {', '.join(missing)}")
    if not args.fixture.is_file():
        raise SystemExit(f"fixture not found: {args.fixture}")


def stamp(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_payload(text: Any) -> dict[str, Any]:
    return {"properties": {"part": {"text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)}}}


def trajectory_events(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert ATIF steps into the final event envelope emitted by the plugin."""
    session_id = str(document.get("session_id") or "replay-session")
    events: list[dict[str, Any]] = []
    fallback_time = datetime(1970, 1, 1, tzinfo=timezone.utc)
    sequence = 0
    for step in document.get("steps", []):
        if not isinstance(step, dict):
            continue
        timestamp = stamp(step.get("timestamp"), fallback_time)
        source = step.get("source")
        if source == "user" and step.get("message"):
            sequence += 1
            events.append({
                "id": f"replay-{sequence:05d}", "type": "USER_MESSAGE_FINAL",
                "source_event": "message.updated", "session_id": session_id,
                "root_session_id": session_id, "timestamp": timestamp.isoformat(),
                "payload": event_payload(step["message"]),
            })
        if step.get("reasoning_content"):
            sequence += 1
            events.append({
                "id": f"replay-{sequence:05d}", "type": "REASONING_FINAL",
                "source_event": "message.part.updated", "session_id": session_id,
                "root_session_id": session_id, "timestamp": timestamp.isoformat(),
                "payload": event_payload(step["reasoning_content"]),
            })
        if step.get("message") and source != "user":
            sequence += 1
            events.append({
                "id": f"replay-{sequence:05d}", "type": "TEXT_FINAL",
                "source_event": "message.part.updated", "session_id": session_id,
                "root_session_id": session_id, "timestamp": timestamp.isoformat(),
                "payload": event_payload(step["message"]),
            })
        for call in step.get("tool_calls", []):
            if not isinstance(call, dict):
                continue
            sequence += 1
            name = call.get("function_name", "unknown")
            events.append({
                "id": f"replay-{sequence:05d}", "type": "TOOL_CALL_FINAL",
                "source_event": "tool.execute.before", "session_id": session_id,
                "root_session_id": session_id, "timestamp": timestamp.isoformat(),
                "payload": event_payload({"tool": name, "arguments": call.get("arguments", {})}),
            })
        observation = step.get("observation")
        if observation is not None:
            sequence += 1
            events.append({
                "id": f"replay-{sequence:05d}", "type": "TOOL_RESULT_FINAL",
                "source_event": "tool.execute.after", "session_id": session_id,
                "root_session_id": session_id, "timestamp": timestamp.isoformat(),
                "payload": event_payload(observation),
            })
    return events


def group_events(events: list[dict[str, Any]], window: float) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    for event in events:
        if not batches:
            batches.append([event])
            continue
        previous = datetime.fromisoformat(batches[-1][-1]["timestamp"])
        current = datetime.fromisoformat(event["timestamp"])
        if (current - previous).total_seconds() <= window:
            batches[-1].append(event)
        else:
            batches.append([event])
    return batches


class ReplayInbox:
    async def pending_count(self, root_session_id: str) -> int:
        return 0


class ReplayOpenCode:
    def __init__(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self.session_id = session_id
        self.messages = messages[-20:]
        self.calls: list[dict[str, Any]] = []

    async def get_context(self, session_id: str) -> dict[str, Any]:
        self.calls.append({"operation": "get_context", "session_id": session_id})
        return {"session_id": session_id, "messages": self.messages}

    async def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        self.calls.append({"operation": "send_message", "session_id": session_id, "message": message})
        return {"simulated": True}

    async def abort_session(self, session_id: str) -> dict[str, Any]:
        self.calls.append({"operation": "abort_session", "session_id": session_id})
        return {"simulated": True}

    async def task_control(self, session_id: str, action: str, **arguments: Any) -> dict[str, Any]:
        self.calls.append({"operation": "task_control", "session_id": session_id, "action": action, "arguments": arguments})
        return {"simulated": True}


class ReplayMemory:
    def __init__(self, *, seeded: bool) -> None:
        self.calls: list[dict[str, Any]] = []
        self.counter = 0
        self.memories: list[dict[str, Any]] = [{
            "id": "replay-memory-prior",
            "category_id": "replay-category-execution",
            "title": "Previous git recovery observation",
            "content": "The lost commit must be recovered from reflog before merging into master.",
            "type": "OBSERVATION", "status": "ACTIVE", "graph_tier": "ACTIVE", "confidence": 0.9,
        }] if seeded else []
        self.edges: list[dict[str, Any]] = []

    async def get_manifest(self) -> dict[str, Any]:
        self.calls.append({"operation": "manifest", "arguments": {}})
        return {"projects": [{
            "id": "replay-project", "name": "personal-site", "keys": [{
                "id": "replay-key-execution", "name": "execution", "categories": [
                    {"id": "replay-category-execution", "name": "execution"},
                    {"id": "replay-category-errors", "name": "errors"},
                    {"id": "replay-category-results", "name": "results"},
                ],
            }],
        }]}

    async def _record(self, operation: str, arguments: dict[str, Any]) -> Any:
        self.counter += 1
        call = {"operation": operation, "arguments": arguments}
        self.calls.append(call)
        if operation == "search":
            # The replay memory is intentionally broad: the real MCP applies
            # hierarchy and text filters, while this fixture supplies candidates
            # for exercising the Supervisor's link policy.
            result = list(self.memories)
            call["result_count"] = len(result)
            return result
        self.counter += 1
        result = {"id": f"replay-memory-{self.counter}", "simulated": True, **arguments}
        if operation == "create":
            self.memories.append(result)
        elif operation == "update":
            memory_id = arguments.get("memory_id")
            for memory in self.memories:
                if memory.get("id") == memory_id:
                    memory.update(arguments)
                    result = memory
        elif operation == "link":
            self.edges.append(result)
        return result

    async def search(self, query: str = "", **filters: Any) -> list[dict[str, Any]]:
        return await self._record("search", {"query": query, **filters})

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        self.calls.append({"operation": "get", "arguments": {"memory_id": memory_id}})
        return next((memory for memory in self.memories if memory.get("id") == memory_id), None)

    async def neighbors(self, memory_id: str, **filters: Any) -> dict[str, Any]:
        return await self._record("neighbors", {"memory_id": memory_id, **filters})

    async def expand(self, memory_id: str, **filters: Any) -> dict[str, Any]:
        return await self._record("expand", {"memory_id": memory_id, **filters})

    async def create(self, memory: dict[str, Any]) -> dict[str, Any]:
        return await self._record("create", memory)

    async def update(self, memory_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        return await self._record("update", {"memory_id": memory_id, **memory})

    async def link(self, source: str, target: str, relation: str, **metadata: Any) -> dict[str, Any]:
        return await self._record("link", {"source": source, "target": target, "relation": relation, **metadata})

    async def archive(self, memory_id: str) -> dict[str, Any]:
        return await self._record("archive", {"memory_id": memory_id})

    async def restore(self, memory_id: str) -> dict[str, Any]:
        return await self._record("restore", {"memory_id": memory_id})


class RecordingModel:
    def __init__(self, model: ReviewModel) -> None:
        self.model = model
        self.calls: list[dict[str, Any]] = []

    async def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            decision = await self.model.decide(context)
        except Exception as error:
            self.calls.append({"duration_ms": round((time.perf_counter() - started) * 1000, 1), "error": type(error).__name__})
            # Preserve the production node's fallback behavior while recording it.
            raise
        self.calls.append({
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "event_count": len(context.get("events", [])),
            "payload_keys": sorted(context),
            "decision": decision,
        })
        return decision


async def replay(args: argparse.Namespace) -> dict[str, Any]:
    document = json.loads(args.fixture.read_text())
    events = trajectory_events(document)
    if args.max_events is not None:
        if args.max_events < 1:
            raise SystemExit("--max-events must be at least 1")
        events = events[:args.max_events]
    if args.max_steps < 1:
        raise SystemExit("--max-steps must be at least 1")
    if not events:
        raise SystemExit("fixture contains no replayable events")
    batches = group_events(events, args.batch_window)
    session_id = events[0]["root_session_id"]
    messages = [{"role": "user", "content": event["payload"]} for event in events if event["type"] == "USER_MESSAGE_FINAL"]
    opencode = ReplayOpenCode(session_id, messages)
    memory = ReplayMemory(seeded=args.memory_mode == "seeded")
    inbox = ReplayInbox()
    model = RecordingModel(OpenAIReviewModel(
        os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        os.environ.get("OPENROUTER_API_KEY"),
        os.environ.get("OPENROUTER_DEPLOYMENT", "deepseek/deepseek-v4-flash-0731"),
        timeout=args.timeout,
    ))
    read_inbox = make_read_inbox_node(limit=20)
    replay_manifest = await memory.get_manifest()
    replay_categories = categories(replay_manifest)
    replay_category_id = str(replay_categories[0]["id"]) if replay_categories else None
    memory_operation = make_memory_operation_node(memory, replay_category_id)
    intervene = make_intervene_node(opencode)
    review_service = ReviewService(memory)
    state: SupervisorState = {"root_session_id": session_id, "current_events": [], "claimed_events": []}
    decisions: list[dict[str, Any]] = []
    batch_reports: list[dict[str, Any]] = []
    try:
        for batch_number, batch in enumerate(batches, start=1):
            state = {**state, "claimed_events": batch, "processing_events": [], "memory_operation_result": None}
            visited: list[str] = []
            completed = False
            for _ in range(args.max_steps):
                model_calls_before = len(model.calls)
                result = await make_review_node(
                    inbox, opencode, review_service, model,
                    memory_category_id=replay_category_id,
                )(state)
                state = {**state, **result}
                visited.append("REVIEW")
                new_calls = model.calls[model_calls_before:]
                decision_source = "deterministic" if not new_calls else "openrouter" if "decision" in new_calls[-1] else "fallback"
                decisions.append({"batch": batch_number, "event_ids": [event["id"] for event in batch], "action": result["next_action"], "reason": result["assessment"]["reason"], "source": decision_source})
                action = result["next_action"]
                if action == "DONE":
                    completed = True
                    break
                if action == "READ_INBOX":
                    state = {**state, **await read_inbox(state)}
                    visited.append("READ_INBOX")
                elif action == "MEMORY_OPERATION":
                    state = {**state, **await memory_operation(state)}
                    visited.append("MEMORY_OPERATION")
                elif action == "INTERVENE":
                    state = {**state, **await intervene(state)}
                    visited.append("INTERVENE")
            if not completed:
                raise RuntimeError(f"replay batch {batch_number} did not reach DONE within {args.max_steps} graph steps")
            batch_reports.append({"batch": batch_number, "events": len(batch), "event_types": Counter(event["type"] for event in batch), "visited": visited})
    finally:
        await model.model.aclose()
    report = {
        "replay_run_id": str(uuid.uuid4()),
        "fixture": str(args.fixture),
        "model": os.environ.get("OPENROUTER_DEPLOYMENT", "deepseek/deepseek-v4-flash-0731"),
        "batch_window_seconds": args.batch_window,
        "events": len(events),
        "batches": len(batches),
        "decisions": decisions,
        "batch_reports": batch_reports,
        "model_calls": model.calls,
        "opencode_simulated_calls": opencode.calls,
        "memory_simulated_calls": memory.calls,
        "action_counts": Counter(item["action"] for item in decisions),
        "event_type_counts": Counter(event["type"] for event in events),
    }
    return json.loads(json.dumps(report, default=dict))


def main() -> int:
    args = parse_args()
    validate_configuration(args)
    report = asyncio.run(replay(args))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
        print(args.output)
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
