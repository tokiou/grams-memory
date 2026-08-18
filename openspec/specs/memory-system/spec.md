# Memory System

## ADDED Requirements

### Requirement: Memory Creation
The system SHALL create memories when significant events occur during agent execution.

#### Scenario: Tool call result
- GIVEN an agent executes a tool call
- WHEN the tool returns a result
- THEN a memory is created with the tool name, input, and output

#### Scenario: Error encountered
- GIVEN an agent encounters an error
- WHEN the error occurs
- THEN a memory is created with error details and context

### Requirement: Memory Retrieval
The system SHALL retrieve relevant memories when making decisions.

#### Scenario: Context injection
- GIVEN the agent processes a new task
- WHEN relevant memories exist
- THEN top-k memories by relevance are injected into context

### Requirement: Memory Decay
The system SHALL discard low-relevance memories over time.

#### Scenario: Capacity limit
- GIVEN memory store is near capacity
- WHEN a new memory needs to be stored
- THEN least-relevant memories are evicted
