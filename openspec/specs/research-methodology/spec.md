# Research Methodology Specification

## ADDED Requirements

### Requirement: Research Questions
The system SHALL address three core research questions.

#### Scenario: Memory content question
- GIVEN the research project is active
- WHEN investigating memory mechanisms
- THEN the system determines what information an agent should remember

#### Scenario: Memory timing question
- GIVEN the research project is active
- WHEN investigating memory mechanisms
- THEN the system determines when memories should be created, updated, retrieved, or discarded

#### Scenario: Memory influence question
- GIVEN the research project is active
- WHEN investigating memory mechanisms
- THEN the system determines how retrieved memories should influence future actions

### Requirement: Experimental Rigor
The system SHALL maintain experimental rigor throughout the research.

#### Scenario: Fixed variables
- GIVEN experiments are conducted
- WHEN comparing Memory ON vs Memory OFF
- THEN the action model (Qwen3.6-35B-A3B) and agent harness (OpenCode) remain fixed

#### Scenario: Additional metrics
- GIVEN experiments are conducted
- WHEN collecting results
- THEN total tokens, memory tokens, memory operations, agent steps, task cost, and latency are recorded

### Requirement: Failure Taxonomy
The system SHALL build a taxonomy of agent failures.

#### Scenario: Failure collection
- GIVEN agent trajectories are collected
- WHEN failures occur
- THEN failures are documented with context and root cause

#### Scenario: Failure categorization
- GIVEN failures are documented
- WHEN analysis is performed
- THEN failures are categorized by type and severity

#### Scenario: Memory design guidance
- GIVEN the failure taxonomy is complete
- WHEN designing the memory system
- THEN the taxonomy informs what memories should be created to prevent failures
