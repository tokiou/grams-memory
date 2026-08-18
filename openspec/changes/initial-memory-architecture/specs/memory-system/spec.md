# Delta for Memory System

## ADDED Requirements

### Requirement: Memory Data Model
The system SHALL define a Memory data structure.

#### Scenario: Memory fields
- GIVEN a memory is created
- WHEN the memory is stored
- THEN it contains: id, content, timestamp, relevance_score, source, metadata

### Requirement: Memory Store Interface
The system SHALL provide a MemoryStore with these operations.

#### Scenario: Add memory
- GIVEN a Memory object
- WHEN add() is called
- THEN the memory is stored and retrievable

#### Scenario: Retrieve by query
- GIVEN memories exist
- WHEN retrieve(query, top_k) is called
- THEN the k most relevant memories are returned

#### Scenario: Evict least relevant
- GIVEN memory store is at capacity
- WHEN add() is called with a new memory
- THEN the memory with lowest relevance_score is removed
