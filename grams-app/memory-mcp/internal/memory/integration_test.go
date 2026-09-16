package memory

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/tokiou/grams-memory/grams-app/memory-mcp/internal/platform/sqlite"
)

func TestHierarchyMemoryAndEdges(t *testing.T) {
	ctx := context.Background()
	db, err := sqlite.New(ctx, filepath.Join(t.TempDir(), "grams.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := sqlite.Migrate(ctx, db); err != nil {
		t.Fatal(err)
	}
	projects := NewProjectRepository(db)
	keys := NewKeyRepository(db)
	categories := NewCategoryRepository(db)
	memories := NewMemoryRepository(db)
	edges := NewEdgeRepository(db)
	svc := NewService(projects, keys, categories, memories, edges)
	p, err := svc.CreateProject(ctx, Project{Name: "repo"})
	if err != nil {
		t.Fatal(err)
	}
	k, err := svc.CreateKey(ctx, Key{ProjectID: p.ID, Name: "architecture"})
	if err != nil {
		t.Fatal(err)
	}
	c1, err := svc.CreateCategory(ctx, Category{KeyID: k.ID, Name: "decisions"})
	if err != nil {
		t.Fatal(err)
	}
	c2, err := svc.CreateCategory(ctx, Category{KeyID: k.ID, Name: "results"})
	if err != nil {
		t.Fatal(err)
	}
	m1, err := svc.CreateMemory(ctx, Memory{CategoryID: c1.ID, Content: "use sqlite", Avoid: []AvoidType{AvoidRewrite}})
	if err != nil {
		t.Fatal(err)
	}
	m2, err := svc.CreateMemory(ctx, Memory{CategoryID: c2.ID, Content: "migration passed"})
	if err != nil {
		t.Fatal(err)
	}
	if found, err := svc.Search(ctx, MemoryFilter{ProjectID: &p.ID, Offset: 1}); err != nil || len(found) != 1 {
		t.Fatalf("search with offset failed: %v (%d results)", err, len(found))
	}
	if _, err = svc.CreateEdge(ctx, MemoryEdge{SourceID: m1.ID, TargetID: m2.ID, Relation: RelationSupports}); err != nil {
		t.Fatal(err)
	}
	got, err := svc.GetMemory(ctx, m1.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Avoid) != 1 || got.Avoid[0] != AvoidRewrite {
		t.Fatalf("unexpected avoid values: %#v", got.Avoid)
	}
	if _, err = svc.ArchiveMemory(ctx, m1.ID); err != nil {
		t.Fatal(err)
	}
	got, err = svc.GetMemory(ctx, m1.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.GraphTier != GraphTierCold || got.ArchivedAt == nil {
		t.Fatalf("memory was not archived: %#v", got)
	}
	if _, err = svc.RestoreMemory(ctx, m1.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = svc.GetMemory(ctx, m1.ID); err != nil {
		t.Fatal(err)
	}
}

func TestCrossProjectEdgesRejected(t *testing.T) {
	ctx := context.Background()
	db, err := sqlite.New(ctx, filepath.Join(t.TempDir(), "grams.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := sqlite.Migrate(ctx, db); err != nil {
		t.Fatal(err)
	}
	pr, kr, cr := NewProjectRepository(db), NewKeyRepository(db), NewCategoryRepository(db)
	mr, er := NewMemoryRepository(db), NewEdgeRepository(db)
	svc := NewService(pr, kr, cr, mr, er)
	p1, _ := svc.CreateProject(ctx, Project{Name: "one"})
	p2, _ := svc.CreateProject(ctx, Project{Name: "two"})
	k1, _ := svc.CreateKey(ctx, Key{ProjectID: p1.ID, Name: "k"})
	k2, _ := svc.CreateKey(ctx, Key{ProjectID: p2.ID, Name: "k"})
	c1, _ := svc.CreateCategory(ctx, Category{KeyID: k1.ID, Name: "c"})
	c2, _ := svc.CreateCategory(ctx, Category{KeyID: k2.ID, Name: "c"})
	m1, _ := svc.CreateMemory(ctx, Memory{CategoryID: c1.ID, Content: "one"})
	m2, _ := svc.CreateMemory(ctx, Memory{CategoryID: c2.ID, Content: "two"})
	if _, err := svc.CreateEdge(ctx, MemoryEdge{SourceID: m1.ID, TargetID: m2.ID, Relation: RelationDependsOn}); err != ErrCrossProjectEdge {
		t.Fatalf("expected cross-project error, got %v", err)
	}
}

func TestProcessLifecycleAndSingleActiveInvariant(t *testing.T) {
	ctx := context.Background()
	db, err := sqlite.New(ctx, filepath.Join(t.TempDir(), "grams.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := sqlite.Migrate(ctx, db); err != nil {
		t.Fatal(err)
	}
	pr, kr, cr := NewProjectRepository(db), NewKeyRepository(db), NewCategoryRepository(db)
	mr, er, pcr := NewMemoryRepository(db), NewEdgeRepository(db), NewProcessRepository(db)
	svc := NewService(pr, kr, cr, mr, er, pcr)
	project, err := svc.CreateProject(ctx, Project{Name: "process-project"})
	if err != nil {
		t.Fatal(err)
	}
	key, err := svc.CreateKey(ctx, Key{ProjectID: project.ID, Name: "strategy-key"})
	if err != nil {
		t.Fatal(err)
	}
	process, err := svc.CreateProcess(ctx, Process{ProjectID: project.ID, KeyID: key.ID, Name: "first strategy"})
	if err != nil {
		t.Fatal(err)
	}
	if process.Status != ProcessStatusActive || process.ClosedAt != nil {
		t.Fatalf("unexpected active process: %#v", process)
	}
	active, err := svc.GetActiveProcess(ctx, project.ID)
	if err != nil || active.ID != process.ID {
		t.Fatalf("active process lookup failed: %v %#v", err, active)
	}
	if _, err = svc.CreateProcess(ctx, Process{ProjectID: project.ID, KeyID: key.ID, Name: "second strategy"}); err == nil {
		t.Fatal("expected one-active-process constraint")
	}
	closed, err := svc.CloseProcess(ctx, process.ID, ProcessStatusSucceeded)
	if err != nil {
		t.Fatal(err)
	}
	if closed.Status != ProcessStatusSucceeded || closed.ClosedAt == nil {
		t.Fatalf("unexpected closed process: %#v", closed)
	}
	if _, err = svc.GetActiveProcess(ctx, project.ID); err != ErrProcessNotFound {
		t.Fatalf("expected no active process, got %v", err)
	}
	key2, err := svc.CreateKey(ctx, Key{ProjectID: project.ID, Name: "strategy-key-2"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = svc.CreateProcess(ctx, Process{ProjectID: project.ID, KeyID: key2.ID, Name: "second strategy", PredecessorID: &process.ID}); err != nil {
		t.Fatal(err)
	}
}

func TestCreateProcessWithMemoryCreatesProcessKeyAndCategoriesAtomically(t *testing.T) {
	ctx := context.Background()
	db, err := sqlite.New(ctx, filepath.Join(t.TempDir(), "grams.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := sqlite.Migrate(ctx, db); err != nil {
		t.Fatal(err)
	}
	pr, kr, cr := NewProjectRepository(db), NewKeyRepository(db), NewCategoryRepository(db)
	mr, er, pcr := NewMemoryRepository(db), NewEdgeRepository(db), NewProcessRepository(db)
	svc := NewService(pr, kr, cr, mr, er, pcr)
	project, err := svc.CreateProject(ctx, Project{Name: "atomic-process-project"})
	if err != nil {
		t.Fatal(err)
	}
	process, err := svc.CreateProcessWithMemory(ctx, Process{ProjectID: project.ID, Name: "process_001"})
	if err != nil {
		t.Fatal(err)
	}
	keys, err := svc.ListKeys(ctx, project.ID)
	if err != nil || len(keys) != 1 || keys[0].ID != process.KeyID {
		t.Fatalf("unexpected process keys: %v %#v", err, keys)
	}
	categories, err := svc.ListCategories(ctx, process.KeyID)
	if err != nil || len(categories) != 3 {
		t.Fatalf("unexpected process categories: %v %#v", err, categories)
	}
	for _, expected := range []string{"STRATEGY", "EVIDENCE", "SUMMARY"} {
		found := false
		for _, category := range categories {
			if category.Name == expected {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("missing process category %q: %#v", expected, categories)
		}
	}
}
