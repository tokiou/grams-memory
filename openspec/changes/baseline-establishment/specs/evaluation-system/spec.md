# Delta for Evaluation System

## ADDED Requirements

### Requirement: Terminal-Bench 2.0 Integration
The system SHALL integrate with Terminal-Bench 2.0 for evaluation.

#### Scenario: Benchmark configuration
- GIVEN Terminal-Bench 2.0 is installed
- WHEN evaluation is configured
- THEN the benchmark is properly set up for agent evaluation

#### Scenario: Task execution
- GIVEN the benchmark is configured
- WHEN tasks are executed
- THEN the agent processes benchmark tasks correctly

### Requirement: OpenCode Integration
The system SHALL integrate with OpenCode as the agent harness.

#### Scenario: OpenCode setup
- GIVEN OpenCode is installed
- WHEN the agent is configured
- THEN OpenCode provides the execution environment

#### Scenario: Model configuration
- GIVEN OpenCode is set up
- WHEN the model is configured
- THEN Qwen3.6-35B-A3B is used for agent actions
