# GRAMS

> Graph-Relational Agent Memory Supervisor

GRAMS is a research project exploring memory mechanisms for long-horizon AI agents.

The project investigates whether an external graph-based memory system can help agents preserve and reuse relevant information across long execution trajectories while keeping the underlying action agent unchanged.

## Idea

Long-horizon agents operate across many steps, tool calls, observations, failures, intermediate decisions, and partial discoveries.

As trajectories grow, information discovered earlier may stop influencing future actions effectively.

GRAMS explores an architecture where a separate Memory Supervisor observes the action-agent trajectory and interacts with an external relational memory system through MCP.

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

The memory system evolves during execution and is intended to represent not only individual memories, but also relationships between information discovered throughout the trajectory.

The exact memory representation, graph structure, retrieval strategy, scoring mechanism, storage policy, and intervention policy are still research questions.

## Current Architecture Hypothesis

Memory is organized per project or repository.

```text
Project / Repository
        │
        ├── Key
        │    ├── Category
        │    │     └── Graph of Memories
        │    └── Category
        │          └── Graph of Memories
        │
        └── Key
             └── ...
```

A project contains a set of Keys. Each Key contains a set of Categories.

Within each Category, memories form a graph and may be connected to one another. When useful, memories may also form relationships with memories belonging to other Categories.

The intended structure is therefore locally organized by Category while still allowing cross-category relations when the Memory Supervisor considers them relevant.

## Preliminary Data Model

The current data model is intentionally minimal and expected to evolve.

### Key
```json
{
    id,
    key_name,
    metadata
}
```

### Category
```json
{
    id,
    key_id,
    category_name,
    metadata
}
```

### Memory Node
```json
{
    id,
    category_id,
    content,
    metadata
}
```

### Memory Edge
```json
{
    source,
    target,
    relation,
    metadata
}
```

The semantics of a memory node, the taxonomy of relations, and the metadata stored on nodes and edges are not fixed yet.

In particular, GRAMS is still investigating:

- what should constitute a single memory;

- how atomic or abstract a memory should be;

- which information from an agent trajectory deserves persistence;

- what relationships should exist between memories;

- what metadata should be stored on nodes and edges.

## Active and Cold Memory

GRAMS currently considers a two-tier memory organization.

```text
GRAMS Memory
     │
     ├── Active Graph
     │     - small
     │     - fast
     │     - supervised
     │     - frequently retrieved
     │
     └── Cold Memory
           - larger
           - historical
            - exceptional / infrequent
```

The Active Graph is intended to contain information likely to be useful during the current execution horizon.

Cold Memory may preserve older, exceptional, or less frequently accessed information that can be restored when needed.

The size limits of Keys, Categories, memories, and the Active Graph are deliberately left open for empirical evaluation.

## Memory Manifest

A project-level Manifest.md is planned as a human- and agent-readable description of how memory is organized for that project.

The manifest may describe:

- existing Keys;
- the purpose of each Key;
- existing Categories;
- the purpose and scope of each Category;
- organizational constraints used by the Memory Supervisor.

Its exact format and update policy are still under investigation.

## MCP Interface

GRAMS plans to expose the graph memory system through a small set of MCP tools.

Initial candidate tools include:

### Candidate Tools

| Tool | Purpose |
| --- | --- |
| `memory_search(query, filters)` | Search memory semantically and/or structurally. |
| `memory_get(id)` | Retrieve a complete memory node. |
| `memory_create(...)` | Create a new memory node. |
| `memory_update(id, ...)` | Update or refine an existing memory. |
| `memory_link(source, target, relation, ...)` | Create a relation between two memories. |
| `memory_neighbors(id, relation?, depth?)` | Traverse neighboring memories in the graph. |
| `memory_archive(id)` | Move a memory out of the Active Graph. |
| `memory_restore(id)` | Restore a memory from Cold Memory. |
| `memory_supersede(old, new)` | Mark old information as replaced by newer information. |
| `memory_context(...)` | Build a compact graph-conditioned context for the current agent state. |

These tools are preliminary.

Low-level memory primitives and higher-level retrieval or context-construction policies may later be separated so that retrieval strategies can be evaluated independently.

## Graph-Conditioned Retrieval

One of the main ideas being explored is whether graph structure can recover useful information that semantic similarity alone would miss.

A semantic search may identify a relevant anchor memory:

```text
query
  │
  ▼
Memory 17
```

GRAMS may then use graph relations to recover a broader relevant subgraph:

```text
Memory 17 ─── Memory 8 ─── Memory 31
```

Conceptually:

```text
[
 q_t \rightarrow M_{17} \rightarrow {M_{17}, M_8, M_{31}}
 ]
```

The hypothesis is that memories with low direct semantic similarity to the current query may still be useful because of their structural relationship to a retrieved memory.

Possible retrieval strategies include:

- semantic retrieval only;

- semantic retrieval + 1-hop graph expansion;

- semantic retrieval + multi-hop traversal;

- adaptive traversal conditioned on query and graph relevance.

## Open Research Questions

GRAMS currently focuses on questions such as:

- What information should an agent remember?

- What constitutes a useful memory for a coding agent?

- How atomic should a memory be?

- When should a memory be created, updated, merged, archived, or discarded?

- How should memories be represented and connected?

- Which relations between memories are useful for future retrieval?

- When should cross-category relations be created?

- How should relevant graph neighborhoods be retrieved?

- When should retrieved memories be injected into the action-agent context?

- When should the Memory Supervisor remain silent?

- How should Active and Cold Memory be managed?

- What memory budgets are useful for long-horizon tasks?

## Memory Discovery Phase

Before fixing the final memory schema, GRAMS will use baseline agent trajectories to study what information has future utility.

The initial process is:

```text
Baseline trajectories
        │
        ▼
Long-horizon failure analysis
        │
        ▼
Candidate memory identification
        │
        ▼
Counterfactual memory injection
        │
        ▼
Empirical memory taxonomy
        │
        ▼
GRAMS memory design
```

A central question is:

What information discovered earlier in a trajectory would change a later agent decision if it were made available again?

This is intended to guide the eventual memory schema using empirical evidence rather than defining memory types only from intuition.

## Research Goal

The central research hypothesis is that an external Memory Supervisor with structured relational memory can improve long-horizon agent behavior without modifying the underlying action agent.

The project aims to study three related components:

- **Memory representation:** what information should be persisted and how it should be structured.
- **Graph-conditioned retrieval:** whether memory relations improve retrieval beyond semantic similarity alone.
- **Memory-conditioned supervision:** when and how retrieved information should influence the action agent.

## Evaluation

Initial experiments will use:

| Item | Selection |
| --- | --- |
| Benchmark | Terminal-Bench 2.0 |
| Model | Qwen3.6-35B-A3B |
| Agent harness | OpenCode |
| Primary metric | Pass@1 |

The core comparison keeps the action model and harness fixed:

```text
Qwen3.6-35B-A3B + OpenCode + Memory OFF

                        vs.

Qwen3.6-35B-A3B + OpenCode + GRAMS
```

Additional metrics may include:

- token usage;
- agent steps;
- memory operations;
- retrieval operations;
- intervention count;
- task cost;
- latency.

Planned ablations may compare:

```text
Baseline
  vs.
Semantic / flat memory
  vs.
Graph-conditioned retrieval
  vs.
Full GRAMS supervision
```

This is intended to separate gains caused by memory availability from gains caused specifically by graph structure or active supervision.

## Current Status

GRAMS is currently in the exploratory stage.

The immediate goals are:

- establish a reproducible Terminal-Bench 2.0 baseline;
- collect complete agent trajectories;
- analyze long-horizon failures and successful information reuse;
- identify candidate memory units empirically;
- design the first minimal GRAMS memory schema;
- implement the initial MCP memory primitives;
- evaluate graph-conditioned retrieval before adding more complex policies.

The methodology and architecture are expected to evolve as the research progresses.
