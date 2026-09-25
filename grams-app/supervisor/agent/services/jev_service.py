"""TypeSafe Jev service for structured Supervisor decisions."""

from __future__ import annotations

import json
from typing import Any
import logging
import os

import httpx

from supervisor.observability import elapsed_ms, emit, monotonic_ns
from supervisor.agent.state_builder import (
    build_jev_process_state,
    compact_jev_state,
    estimate_json_tokens,
)

logger = logging.getLogger(__name__)

MAX_CONTEXT_RETRIES = 3


class _JevPayloadBudgetError(ValueError):
    """A request cannot fit locally and may fit after splitting its questions."""

    def __init__(self, message: str, *, questions_can_be_split: bool = False) -> None:
        super().__init__(message)
        self.questions_can_be_split = questions_can_be_split


class JevClient:
    """Async client for TypeSafe System One evaluations."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_context_tokens: int | None = None,
        timeout: float = 30.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "")
        self.base_url = (base_url or os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1")).rstrip("/")
        self.model = model or os.getenv("TYPESAFE_MODEL", "jev-latest")
        # Keep the complete serialized request below the documented 32k context
        # limit. The tokenizer is not published, so UTF-8 bytes are a
        # conservative upper bound for token count.
        self.max_context_tokens = max_context_tokens if max_context_tokens is not None else int(
            os.getenv("TYPESAFE_CONTEXT_MAX_TOKENS", "32768")
        )
        if (
            not isinstance(self.max_context_tokens, int)
            or isinstance(self.max_context_tokens, bool)
            or self.max_context_tokens <= 0
        ):
            raise ValueError("max_context_tokens must be a positive integer")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    def fits_single_question(self, *, state: dict[str, Any], name: str, question: dict[str, Any]) -> bool:
        """Preflight a complete, compacted request without sending it.

        Dynamic choice builders use this to discard old options before a
        single (unsplittable) question reaches the request pipeline.
        """
        questions = {name: question}
        overhead = estimate_json_tokens({"model": self.model, "state": {}, "questions": questions}) - 2
        available = self.max_context_tokens - overhead
        if available <= 0:
            return False
        compacted = compact_jev_state(state, max_tokens=available)
        return estimate_json_tokens({"model": self.model, "state": compacted, "questions": questions}) <= self.max_context_tokens

    async def system_one(
        self,
        *,
        state: Any,
        questions: dict[str, dict[str, Any]],
        model: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is required")
        selected_model = model or self.model
        state = build_jev_process_state(state) if isinstance(state, dict) and "process_context" in state else state

        try:
            return await self._system_one_with_question_splitting(
                state=state,
                questions=questions,
                model=selected_model,
            )
        except _JevPayloadBudgetError as error:
            emit(
                logger,
                logging.ERROR,
                "jev_request_budget_exhausted",
                service="jev",
                model=selected_model,
                question_count=len(questions),
                context_budget_bytes=self.max_context_tokens,
                error=str(error),
            )
            raise

    async def _system_one_with_question_splitting(
        self,
        *,
        state: Any,
        questions: dict[str, dict[str, Any]],
        model: str,
    ) -> dict[str, Any]:
        # Curation asks many questions per candidate. Split batches need only
        # the candidates addressed by their own question names, not every
        # unrelated extraction in the cycle.
        if isinstance(state, dict) and isinstance(state.get("memory_candidates"), list):
            candidates = state["memory_candidates"]
            referenced_targets = {
                ref
                for name, question in questions.items()
                if name.endswith("_relation_target") and isinstance(question, dict)
                for ref in (question.get("criteria") or {})
            }
            addressed = [
                candidate for candidate in candidates
                if isinstance(candidate, dict)
                and (
                    any(name.startswith(str(candidate.get("candidate_ref", "")) + "_") for name in questions)
                    or candidate.get("candidate_ref") in referenced_targets
                )
            ]
            if addressed:
                state = {**state, "memory_candidates": addressed}
        try:
            return await self._system_one_with_compaction_retries(
                state=state,
                questions=questions,
                model=model,
            )
        except (_JevPayloadBudgetError, httpx.HTTPStatusError) as error:
            items = list(questions.items())
            can_split = (
                error.questions_can_be_split
                if isinstance(error, _JevPayloadBudgetError)
                else self._is_request_size_error(error)
            )
            if not can_split:
                raise
            if len(items) < 2:
                if not isinstance(error, _JevPayloadBudgetError):
                    raise
                question_name = items[0][0] if items else "<none>"
                raise _JevPayloadBudgetError(
                    f"Jev question {question_name!r} cannot fit the configured request byte limit",
                ) from error

            midpoint = len(items) // 2
            left_questions = dict(items[:midpoint])
            right_questions = dict(items[midpoint:])
            emit(
                logger,
                logging.WARNING,
                "jev_questions_split_for_budget",
                service="jev",
                model=model,
                question_count=len(items),
                left_question_count=len(left_questions),
                right_question_count=len(right_questions),
                error=str(error),
            )
            left = await self._system_one_with_question_splitting(
                state=state,
                questions=left_questions,
                model=model,
            )
            right = await self._system_one_with_question_splitting(
                state=state,
                questions=right_questions,
                model=model,
            )
            merged = self._merge_question_responses(left, right)
            expected_answers = {name for name, _ in items}
            actual_answers = set(merged["answers"])
            if expected_answers != actual_answers:
                missing = sorted(expected_answers - actual_answers)
                unexpected = sorted(actual_answers - expected_answers)
                raise RuntimeError(
                    "TypeSafe returned incomplete answers for split question batches "
                    f"(missing={missing}, unexpected={unexpected})"
                )
            return merged

    async def _system_one_with_compaction_retries(
        self,
        *,
        state: Any,
        questions: dict[str, dict[str, Any]],
        model: str,
    ) -> dict[str, Any]:
        request_without_state = {
            "model": model,
            "state": {},
            "questions": questions,
        }
        request_overhead = estimate_json_tokens(request_without_state) - 2
        state_limit = self.max_context_tokens - request_overhead
        if state_limit <= 0:
            raise _JevPayloadBudgetError(
                "Jev questions cannot fit the configured request byte limit",
                questions_can_be_split=True,
            )

        previous_state_content: str | None = None
        previous_payload: bytes | None = None
        last_size_error: httpx.HTTPStatusError | None = None
        for attempt in range(MAX_CONTEXT_RETRIES + 1):
            # After a provider-side size rejection, reduce the state budget
            # geometrically. The initial attempt uses the full available budget.
            compact_budget = max(1, state_limit // (2**attempt))
            compacted_state = compact_jev_state(state, max_tokens=compact_budget)
            request_body = {"model": model, "state": compacted_state, "questions": questions}
            serialized_request = json.dumps(
                request_body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
            state_bytes = estimate_json_tokens(compacted_state)
            request_bytes = len(serialized_request)
            if request_bytes > self.max_context_tokens:
                minimum_body = json.dumps(
                    {"model": model, "state": compacted_state, "questions": {}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                raise _JevPayloadBudgetError(
                    "Jev context cannot fit the configured request byte limit after compaction",
                    questions_can_be_split=len(minimum_body) <= self.max_context_tokens,
                )

            state_content = dict(compacted_state)
            state_content.pop("context_compaction", None)
            state_signature = json.dumps(
                state_content, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            )
            if state_signature == previous_state_content or serialized_request == previous_payload:
                if last_size_error is not None:
                    emit(
                        logger,
                        logging.ERROR,
                        "jev_context_compaction_exhausted",
                        service="jev",
                        model=model,
                        attempts=attempt,
                        request_payload_bytes=request_bytes,
                        context_budget_bytes=self.max_context_tokens,
                        status_code=last_size_error.response.status_code,
                        reason="context_not_reducible",
                    )
                    raise last_size_error
                raise _JevPayloadBudgetError(
                    "Jev context cannot fit the configured request byte limit after compaction"
                )
            previous_state_content = state_signature
            previous_payload = serialized_request

            emit(
                logger,
                logging.INFO,
                "model_call_started",
                service="jev",
                model=model,
                prompt_version="v2",
                questions=sorted(questions),
                question_count=len(questions),
                context_budget_bytes=self.max_context_tokens,
                state_bytes=state_bytes,
                request_bytes=request_bytes,
                context_compacted=bool(compacted_state.get("context_compaction")),
                attempt=attempt + 1,
                max_attempts=MAX_CONTEXT_RETRIES + 1,
            )
            attempt_started = monotonic_ns()
            try:
                response = await self._http.post(
                    f"{self.base_url}/systemone",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    content=serialized_request,
                )
                response.raise_for_status()
                body = response.json()
            except Exception as error:
                error_fields = {
                    "error": type(error).__name__,
                    "duration_ms": elapsed_ms(attempt_started),
                    "attempt": attempt + 1,
                    "max_attempts": MAX_CONTEXT_RETRIES + 1,
                }
                if isinstance(error, httpx.HTTPStatusError):
                    error_response = error.response
                    error_fields.update({
                        "status_code": error_response.status_code,
                        "request_id": error_response.headers.get("x-typesafe-request-id"),
                        "request_payload_bytes": request_bytes,
                    })
                emit(logger, logging.ERROR, "model_call_failed", service="jev", model=model,
                     prompt_version="v2", **error_fields)
                if not self._is_request_size_error(error):
                    raise
                last_size_error = error
                if attempt >= MAX_CONTEXT_RETRIES:
                    emit(
                        logger,
                        logging.ERROR,
                        "jev_context_compaction_exhausted",
                        service="jev",
                        model=model,
                        attempts=attempt + 1,
                        request_payload_bytes=request_bytes,
                        context_budget_bytes=self.max_context_tokens,
                        status_code=error.response.status_code,
                    )
                    raise
                emit(
                    logger,
                    logging.WARNING,
                    "jev_context_compaction_retry",
                    service="jev",
                    model=model,
                    attempt=attempt + 1,
                    next_attempt=attempt + 2,
                    request_payload_bytes=request_bytes,
                    state_bytes=state_bytes,
                    context_budget_bytes=self.max_context_tokens,
                    status_code=error.response.status_code,
                )
                continue

            if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
                raise RuntimeError("TypeSafe returned an invalid System One response")
            emit(logger, logging.INFO, "model_call_completed", service="jev", model=model,
                 prompt_version="v2", response_model=body.get("model"), model_version=body.get("version"),
                 questions=sorted(questions), duration_ms=elapsed_ms(attempt_started), usage=body.get("usage"),
                 attempt=attempt + 1)
            return body

        raise RuntimeError("Jev context compaction retry loop exited unexpectedly")

    @staticmethod
    def _is_request_size_error(error: Exception) -> bool:
        if not isinstance(error, httpx.HTTPStatusError):
            return False
        response = error.response
        if response.status_code == 413:
            return True
        if response.status_code != 400:
            return False
        response_text = response.text.lower()
        return any(marker in response_text for marker in (
            "context length",
            "maximum context",
            "context window",
            "exceed context",
            "too many tokens",
            "input token limit",
            "maximum input tokens",
            "input tokens exceed",
            "token limit",
            "token count exceeds",
            "maximum token count",
            "size limit",
            "byte limit",
            "request too large",
            "payload too large",
            "input too long",
            "prompt too long",
        ))

    @staticmethod
    def _merge_question_responses(
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> dict[str, Any]:
        left_answers = left["answers"]
        right_answers = right["answers"]
        duplicates = set(left_answers).intersection(right_answers)
        if duplicates:
            raise RuntimeError("TypeSafe returned duplicate answers for split question batches")
        # Preserve the envelope union; on conflicting non-usage metadata, the
        # later batch is authoritative. Usage counters are additive below.
        result = {**left, **right, "answers": {**left_answers, **right_answers}}
        if "usage" in left or "usage" in right:
            result["usage"] = JevClient._merge_usage(left.get("usage"), right.get("usage"))
        return result

    @staticmethod
    def _merge_usage(left: Any, right: Any) -> Any:
        if isinstance(left, dict) and isinstance(right, dict):
            merged = dict(left)
            for key, value in right.items():
                merged[key] = JevClient._merge_usage(merged[key], value) if key in merged else value
            return merged
        if (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        ):
            return left + right
        return right if right is not None else left

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
