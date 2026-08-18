# Proposal: Initial Memory Architecture

## Intent
Define the core memory system for long-horizon agent execution.

## Scope
- Memory creation on significant events
- Memory retrieval by relevance
- Memory eviction by decay

## Out of Scope
- Specific embedding model selection
- Persistence format (file vs DB)
- UI/visualization tools

## Approach
Start with in-memory store, simple keyword relevance, and LRU eviction.
