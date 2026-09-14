"""Structured output contracts for the GRAMS Supervisor.

These types describe node boundaries only. Parsing, validation, and external
service integration will be implemented in later iterations.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class ProcessContinuityDecision(TypedDict, total=False):
    decision: Literal["SAME_PROCESS", "NEW_PROCESS"]
    reason: str
    suggested_process_name: str


class MemoryCandidate(TypedDict):
    category: Literal["STRATEGY", "EVIDENCE"]
    title: str
    content: str


class RelationCandidate(TypedDict):
    source_id: str
    relation_type: str
    target_id: str


class MemoryUpdateProposal(TypedDict):
    memories: list[MemoryCandidate]
    relations: list[RelationCandidate]


class ReviewDecision(TypedDict, total=False):
    action: Literal["CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"]
    reason: str
    memory_ids: list[str]
    relation_types: list[str]
    related_process_ids: list[str]
    evidence_ids: list[str]
    guidance: str
    process_outcome: Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]


class ProcessSummary(TypedDict, total=False):
    content: str
    outcome: Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]


class ProgressStall(TypedDict):
    detected: bool
    observations: list[str]
