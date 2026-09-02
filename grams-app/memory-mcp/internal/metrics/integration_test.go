package metrics

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/tokiou/grams-memory/grams-app/memory-mcp/internal/platform/sqlite"
)

func TestSnapshotReportsHierarchyAndGraphMetrics(t *testing.T) {
	ctx := context.Background()
	db, err := sqlite.New(ctx, filepath.Join(t.TempDir(), "grams.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := sqlite.Migrate(ctx, db); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{
		`INSERT INTO projects VALUES ('p1', 'project', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')`,
		`INSERT INTO keys VALUES ('k1', 'p1', 'key', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')`,
		`INSERT INTO categories VALUES ('c1', 'k1', 'one', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')`,
		`INSERT INTO categories VALUES ('c2', 'k1', 'two', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')`,
		`INSERT INTO memories VALUES ('m1', 'c1', 'fact', '', '', 'FACT', 'CONFIRMED', 'ACTIVE', 0.8, 'test', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL)`,
		`INSERT INTO memories VALUES ('m2', 'c2', 'result', '', '', 'RESULT', 'ACTIVE', 'COLD', 0.6, 'test', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')`,
		`INSERT INTO memory_avoid VALUES ('m1', 'REWRITE')`,
		`INSERT INTO memory_edges VALUES ('e1', 'm1', 'm2', 'SUPPORTS', 0.9, 'STRONG', 1, 'test', '2026-01-01T00:00:00Z')`,
	} {
		if _, err := db.ExecContext(ctx, query); err != nil {
			t.Fatal(err)
		}
	}
	result, err := NewRepository(db).Snapshot(ctx, "p1")
	if err != nil {
		t.Fatal(err)
	}
	if result.ProjectCount != 1 || result.KeyCount != 1 || result.CategoryCount != 2 || result.MemoryCount != 2 || result.EdgeCount != 1 {
		t.Fatalf("unexpected totals: %#v", result)
	}
	if result.ActiveMemoryCount != 1 || result.ColdMemoryCount != 1 || result.MemoriesWithoutEdges != 0 {
		t.Fatalf("unexpected lifecycle metrics: %#v", result)
	}
	if result.CrossCategoryEdges != 1 || result.CrossKeyEdges != 0 || len(result.EdgesByRelation) != 1 {
		t.Fatalf("unexpected graph metrics: %#v", result)
	}
	if len(result.MemoryTypes) != 2 || len(result.AvoidTypes) != 1 || len(result.CategoriesByHierarchy) != 2 {
		t.Fatalf("unexpected breakdowns: %#v", result)
	}
	global, err := NewRepository(db).Snapshot(ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	if global.ProjectCount != 1 || global.MemoryCount != 2 || global.EdgeCount != 1 {
		t.Fatalf("unexpected global totals: %#v", global)
	}
	graph, err := NewRepository(db).Reconstruct(ctx, "p1")
	if err != nil {
		t.Fatal(err)
	}
	if len(graph.Projects) != 1 || len(graph.Projects[0].Keys) != 1 || len(graph.Projects[0].Keys[0].Categories) != 2 || len(graph.Projects[0].Edges) != 1 {
		t.Fatalf("unexpected reconstructed graph: %#v", graph)
	}
}
