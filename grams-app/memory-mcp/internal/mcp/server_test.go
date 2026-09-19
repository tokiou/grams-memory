package mcpserver

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/tokiou/grams-memory/grams-app/memory-mcp/internal/memory"
	"github.com/tokiou/grams-memory/grams-app/memory-mcp/internal/platform/sqlite"
	"path/filepath"
)

func TestProcessGetActiveIsPublishedAndCallable(t *testing.T) {
	ctx := context.Background()
	db, err := sqlite.New(ctx, filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := sqlite.Migrate(ctx, db); err != nil {
		t.Fatal(err)
	}
	projects := memory.NewProjectRepository(db)
	keys := memory.NewKeyRepository(db)
	categories := memory.NewCategoryRepository(db)
	memories := memory.NewMemoryRepository(db)
	edges := memory.NewEdgeRepository(db)
	processes := memory.NewProcessRepository(db)
	service := memory.NewService(projects, keys, categories, memories, edges, processes)
	graph := memory.NewGraphService(memories, edges)
	project, err := service.CreateProject(ctx, memory.Project{Name: "mcp-contract"})
	if err != nil {
		t.Fatal(err)
	}
	process, err := service.CreateProcessWithMemory(ctx, memory.Process{ProjectID: project.ID, Name: "process_001"})
	if err != nil {
		t.Fatal(err)
	}

	server := New(service, graph)
	clientTransport, serverTransport := mcp.NewInMemoryTransports()
	serverSession, err := server.Connect(ctx, serverTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer serverSession.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "contract-test", Version: "0.1.0"}, nil)
	clientSession, err := client.Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer clientSession.Close()

	tools, err := clientSession.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, tool := range tools.Tools {
		if tool.Name == "process_get_active" {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("process_get_active was not published by the MCP server")
	}
	var processTool *mcp.Tool
	for _, tool := range tools.Tools {
		if tool.Name == "process_get_active" {
			processTool = tool
			break
		}
	}
	schemaBytes, err := json.Marshal(processTool.InputSchema)
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]any
	if err := json.Unmarshal(schemaBytes, &schema); err != nil {
		t.Fatal(err)
	}
	properties, ok := schema["properties"].(map[string]any)
	if !ok {
		t.Fatalf("process_get_active schema has no properties: %s", schemaBytes)
	}
	idProperty, ok := properties["id"].(map[string]any)
	if !ok || idProperty["type"] != "string" {
		t.Fatalf("process_get_active schema has invalid id property: %s", schemaBytes)
	}
	required, ok := schema["required"].([]any)
	if !ok || len(required) != 1 || required[0] != "id" {
		t.Fatalf("process_get_active schema does not require id: %s", schemaBytes)
	}

	result, err := clientSession.CallTool(ctx, &mcp.CallToolParams{
		Name:      "process_get_active",
		Arguments: map[string]any{"id": project.ID},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.IsError {
		t.Fatalf("process_get_active returned a tool error: %#v", result.Content)
	}
	if result.StructuredContent == nil {
		t.Fatal("process_get_active returned no structured content")
	}
	encoded, err := json.Marshal(result.StructuredContent)
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(encoded, &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ID"] != string(process.ID) || payload["ProjectID"] != string(project.ID) || payload["Status"] != string(memory.ProcessStatusActive) {
		t.Fatalf("unexpected process payload: %s", encoded)
	}

	emptyResult, err := clientSession.CallTool(ctx, &mcp.CallToolParams{
		Name:      "process_get_active",
		Arguments: map[string]any{"id": ""},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !emptyResult.IsError {
		t.Fatal("empty project id should be a tool error")
	}
}
