PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(id)
);
CREATE TABLE IF NOT EXISTS keys (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_id, name), UNIQUE(project_id, id)
);
CREATE TABLE IF NOT EXISTS processes (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  key_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  predecessor_id TEXT,
  started_at TEXT NOT NULL, closed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK(status IN ('ACTIVE', 'SUCCEEDED', 'FAILED', 'ABANDONED', 'SUPERSEDED')),
  CHECK((status = 'ACTIVE' AND closed_at IS NULL) OR (status <> 'ACTIVE' AND closed_at IS NOT NULL)),
  CHECK(predecessor_id IS NULL OR predecessor_id <> id),
  UNIQUE(project_id, name), UNIQUE(project_id, id),
  FOREIGN KEY(project_id, key_id) REFERENCES keys(project_id, id) ON DELETE CASCADE,
  FOREIGN KEY(project_id, predecessor_id) REFERENCES processes(project_id, id)
);
CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY, key_id TEXT NOT NULL REFERENCES keys(id) ON DELETE CASCADE,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(key_id, name)
);
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY, category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  content TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL, status TEXT NOT NULL, graph_tier TEXT NOT NULL,
  confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1), source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT
);
CREATE TABLE IF NOT EXISTS memory_avoid (
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  avoid_type TEXT NOT NULL, PRIMARY KEY(memory_id, avoid_type)
);
CREATE TABLE IF NOT EXISTS memory_edges (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  relation TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
  evidence_strength TEXT NOT NULL, direct INTEGER NOT NULL CHECK(direct IN (0, 1)),
  source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, CHECK(source_id <> target_id)
);
CREATE INDEX IF NOT EXISTS idx_keys_project ON keys(project_id);
CREATE INDEX IF NOT EXISTS idx_processes_project ON processes(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_processes_one_active_per_project ON processes(project_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_categories_key ON categories(key_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category_id);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(graph_tier);
CREATE INDEX IF NOT EXISTS idx_avoid_memory ON memory_avoid(memory_id);
CREATE INDEX IF NOT EXISTS idx_avoid_type ON memory_avoid(avoid_type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON memory_edges(relation);
CREATE INDEX IF NOT EXISTS idx_edges_source_relation ON memory_edges(source_id, relation);
CREATE INDEX IF NOT EXISTS idx_edges_target_relation ON memory_edges(target_id, relation);
