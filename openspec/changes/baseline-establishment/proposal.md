# Proposal: Establish Terminal-Bench 2.0 Baseline

## Intent
Establish a reproducible no-memory baseline using Terminal-Bench 2.0 to collect agent trajectories for failure analysis.

## Scope
- Set up Terminal-Bench 2.0 evaluation environment
- Integrate OpenCode with Qwen3.6-35B-A3B model
- Execute baseline evaluations without memory
- Collect and analyze agent trajectories

## Approach
1. Configure Terminal-Bench 2.0 evaluation harness
2. Set up OpenCode integration with Qwen3.6-35B-A3B
3. Execute evaluation tasks without memory enabled
4. Record complete trajectories for analysis
5. Collect Pass@1 metrics and additional performance data

## Expected Outcomes
- Reproducible baseline metrics
- Collection of agent trajectories for failure analysis
- Understanding of where and how the baseline agent fails during long-horizon execution
- Foundation for designing memory architecture

## Research Questions Addressed
- What are the baseline performance characteristics?
- Where does the agent fail in long-horizon tasks?
- What patterns emerge from trajectory analysis?

## Risk Assessment
- Low risk: Baseline establishment is a foundational step
- Mitigation: Use established benchmark and model configurations
