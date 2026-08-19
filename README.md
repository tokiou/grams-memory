# GRAMS

**Graph-Relational Agent Memory Supervisor**

GRAMS is a research project exploring memory mechanisms for long-horizon AI agents.

The project investigates whether an external graph-based memory system can help agents preserve and reuse relevant information across long execution trajectories while keeping the underlying action agent unchanged.

## Idea

Long-horizon agents operate across many steps, tool calls, observations, failures, and intermediate decisions.

As trajectories grow, information discovered earlier may stop influencing future actions effectively.

GRAMS explores an architecture where a separate **Memory Supervisor** observes the agent trajectory and interacts with an external relational memory system through MCP.

```text
Action Agent
     │
     │ trajectory
     ▼
Memory Supervisor
     │
     │ MCP
     ▼
Graph Memory
```

The memory system is expected to evolve during execution and represent relationships between information discovered throughout the trajectory.

The exact memory representation, graph structure, retrieval strategy, scoring mechanism, and intervention policy are still research questions.

## Research Goal

The project focuses on questions such as:

* What information should an agent remember?
* How should memories be represented and connected?
* When should memories be created, updated, retrieved, or discarded?
* How should retrieved memories influence future actions?

## Evaluation

Initial experiments will use:

* **Benchmark:** Terminal-Bench 2.0
* **Model:** Qwen3.6-35B-A3B
* **Agent harness:** OpenCode
* **Primary metric:** Pass@1

The core comparison keeps the action model and harness fixed:

```text
Qwen3.6-35B-A3B + OpenCode + Memory OFF
                        vs.
Qwen3.6-35B-A3B + OpenCode + GRAMS
```

Additional metrics may include tokens, agent steps, memory operations, task cost, and latency.

## Current Status

GRAMS is currently in the exploratory stage.

The first goal is to establish a reproducible Terminal-Bench 2.0 baseline, collect agent trajectories, analyze long-horizon failures, and use those results to design the memory architecture.

The methodology and architecture are expected to evolve as the research progresses.
