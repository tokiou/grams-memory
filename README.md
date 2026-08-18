# Long-Horizon Memory

Research project exploring memory mechanisms for long-horizon AI agents.

The project investigates whether explicit memory mechanisms can improve agent performance over extended trajectories while keeping the underlying action model fixed.

## Research Goal

Long-horizon agents often operate across many steps, tool calls, failures, observations, and intermediate decisions. Over time, information discovered earlier in a trajectory may stop influencing future actions effectively.

This project studies how different memory mechanisms can help agents preserve and use relevant information throughout long-running tasks.

The initial research focuses on three questions:

- What information should an agent remember?
- When should memories be created, updated, retrieved, or discarded?
- How should retrieved memories influence future actions?

## Benchmark

The primary evaluation environment is **Terminal-Bench 2.0**, a benchmark for evaluating AI agents on real-world terminal tasks.

Initial experiments will use:

- **Benchmark:** Terminal-Bench 2.0
- **Model:** Qwen3.6-35B-A3B
- **Agent harness:** OpenCode
- **Primary metric:** Pass@1

## Experimental Setup

The core comparison will keep the action model and agent harness fixed:

    Qwen3.6-35B-A3B + OpenCode + Memory OFF
                            vs.
    Qwen3.6-35B-A3B + OpenCode + Memory ON

This allows us to isolate the effect of the memory system from improvements caused by changing the underlying model or agent harness.

Additional metrics may include:

- Total tokens
- Memory tokens
- Number of memory operations
- Agent steps
- Task cost
- Latency

## Current Status

The project is currently in the exploratory stage.

The first milestone is to establish a reproducible Terminal-Bench 2.0 baseline and collect agent trajectories for failure analysis.

The memory architecture will be designed after studying where and how the baseline agent fails during long-horizon execution.

## Roadmap

1. Set up Terminal-Bench 2.0 evaluation
2. Integrate OpenCode and Qwen3.6-35B-A3B
3. Establish a no-memory baseline
4. Collect and analyze trajectories
5. Build a failure taxonomy
6. Design the memory methodology
7. Evaluate memory-enabled agents
8. Run ablations and full benchmark experiments

## Research

This repository is experimental and under active development.

The methodology, architecture, and evaluation protocol are expected to evolve as the research progresses.

## Planned Evaluation Protocol

We will follow the methodology from *Remember When It Matters: Proactive Memory Agent for Long-Horizon Agents* (Wu et al., 2026 — [arXiv:2607.08716](https://arxiv.org/abs/2607.08716)):

- **Memory agent**: A separate module runs alongside the unmodified action agent, updating a structured memory bank (status/knowledge/procedural entries) and deciding whether to inject a reminder or remain silent at each step
- **Trigger interval**: Every step (N=1), with a trajectory window of k=8 messages
- **Two-phase operation**: Phase 1 updates the bank, Phase 2 selects intervention or silence
- **Ablations**: Full-bank context (no intervention selection), always inject (no silence), injection-only (no persistent bank)

### Planned Comparisons

| Condition | Action Model | Harness | Memory | Metric |
|-----------|-------------|---------|--------|--------|
| Baseline | Qwen3.6-35B-A3B | OpenCode | OFF | Pass@1 |
| Memory ON | Qwen3.6-35B-A3B | OpenCode | ON | Pass@1 + Δ |

Primary benchmark: Terminal-Bench 2.0 (89 tasks). Secondary: τ²-Bench (optional).

Reporting: pass@1 percentages, Δ in percentage points (pp), per-task logs and trajectories.
