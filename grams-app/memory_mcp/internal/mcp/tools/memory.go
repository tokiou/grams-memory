package tools

import (
	"context"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/memory"
	"time"
)

type projectInput struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
}
type keyInput struct {
	ProjectID   memory.ProjectID `json:"project_id"`
	Name        string           `json:"name"`
	Description string           `json:"description,omitempty"`
}
type categoryInput struct {
	KeyID       memory.KeyID `json:"key_id"`
	Name        string       `json:"name"`
	Description string       `json:"description,omitempty"`
}
type idInput struct {
	ID string `json:"id"`
}
type memoryInput struct {
	ID          memory.MemoryID     `json:"id,omitempty"`
	CategoryID  memory.CategoryID   `json:"category_id"`
	Content     string              `json:"content"`
	Title       string              `json:"title,omitempty"`
	Description string              `json:"description,omitempty"`
	Type        memory.MemoryType   `json:"type,omitempty"`
	Status      memory.MemoryStatus `json:"status,omitempty"`
	GraphTier   memory.GraphTier    `json:"graph_tier,omitempty"`
	Avoid       []memory.AvoidType  `json:"avoid,omitempty"`
	Confidence  *float64            `json:"confidence,omitempty"`
	Source      string              `json:"source,omitempty"`
}
type edgeInput struct {
	SourceID         memory.MemoryID         `json:"source_id"`
	TargetID         memory.MemoryID         `json:"target_id"`
	Relation         memory.RelationType     `json:"relation"`
	Confidence       *float64                `json:"confidence,omitempty"`
	EvidenceStrength memory.EvidenceStrength `json:"evidence_strength,omitempty"`
	Direct           bool                    `json:"direct,omitempty"`
	Source           string                  `json:"source,omitempty"`
}
type searchInput struct {
	ProjectID     *memory.ProjectID     `json:"project_id,omitempty"`
	KeyID         *memory.KeyID         `json:"key_id,omitempty"`
	CategoryID    *memory.CategoryID    `json:"category_id,omitempty"`
	Types         []memory.MemoryType   `json:"types,omitempty"`
	Statuses      []memory.MemoryStatus `json:"statuses,omitempty"`
	GraphTiers    []memory.GraphTier    `json:"graph_tiers,omitempty"`
	Avoid         []memory.AvoidType    `json:"avoid,omitempty"`
	MinConfidence *float64              `json:"min_confidence,omitempty"`
	Limit         int                   `json:"limit,omitempty"`
	Offset        int                   `json:"offset,omitempty"`
}
type neighborInput struct {
	ID         memory.MemoryID       `json:"id"`
	Depth      int                   `json:"depth,omitempty"`
	Relations  []memory.RelationType `json:"relations,omitempty"`
	GraphTiers []memory.GraphTier    `json:"graph_tiers,omitempty"`
}

func confidence(value *float64) float64 {
	if value == nil {
		return 1
	}
	return *value
}
func RegisterMemoryTools(s *mcp.Server, svc *memory.Service, graph *memory.GraphService) {
	mcp.AddTool(s, &mcp.Tool{Name: "project_create", Description: "Create a project"}, func(ctx context.Context, _ *mcp.CallToolRequest, in projectInput) (*mcp.CallToolResult, memory.Project, error) {
		p, e := svc.CreateProject(ctx, memory.Project{Name: in.Name, Description: in.Description})
		return nil, p, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "project_get", Description: "Get a project"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, *memory.Project, error) {
		p, e := svc.GetProject(ctx, memory.ProjectID(in.ID))
		return nil, p, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "project_list", Description: "List projects"}, func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, []memory.Project, error) {
		p, e := svc.ListProjects(ctx)
		return nil, p, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "key_create", Description: "Create a key"}, func(ctx context.Context, _ *mcp.CallToolRequest, in keyInput) (*mcp.CallToolResult, memory.Key, error) {
		k, e := svc.CreateKey(ctx, memory.Key{ProjectID: in.ProjectID, Name: in.Name, Description: in.Description})
		return nil, k, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "key_get", Description: "Get a key"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, *memory.Key, error) {
		k, e := svc.GetKey(ctx, memory.KeyID(in.ID))
		return nil, k, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "key_list", Description: "List project keys"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, []memory.Key, error) {
		k, e := svc.ListKeys(ctx, memory.ProjectID(in.ID))
		return nil, k, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "category_create", Description: "Create a category"}, func(ctx context.Context, _ *mcp.CallToolRequest, in categoryInput) (*mcp.CallToolResult, memory.Category, error) {
		c, e := svc.CreateCategory(ctx, memory.Category{KeyID: in.KeyID, Name: in.Name, Description: in.Description})
		return nil, c, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "category_get", Description: "Get a category"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, *memory.Category, error) {
		c, e := svc.GetCategory(ctx, memory.CategoryID(in.ID))
		return nil, c, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "category_list", Description: "List key categories"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, []memory.Category, error) {
		c, e := svc.ListCategories(ctx, memory.KeyID(in.ID))
		return nil, c, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_create", Description: "Create a memory"}, func(ctx context.Context, _ *mcp.CallToolRequest, in memoryInput) (*mcp.CallToolResult, memory.Memory, error) {
		m, e := svc.CreateMemory(ctx, memory.Memory{CategoryID: in.CategoryID, Content: in.Content, Title: in.Title, Description: in.Description, Type: in.Type, Status: in.Status, GraphTier: in.GraphTier, Avoid: in.Avoid, Confidence: confidence(in.Confidence), Source: in.Source})
		return nil, m, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_get", Description: "Get a memory"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, *memory.Memory, error) {
		m, e := svc.GetMemory(ctx, memory.MemoryID(in.ID))
		return nil, m, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_update", Description: "Update a memory"}, func(ctx context.Context, _ *mcp.CallToolRequest, in memoryInput) (*mcp.CallToolResult, memory.Memory, error) {
		value := confidence(in.Confidence)
		var archivedAt *time.Time
		if in.Confidence == nil {
			current, err := svc.GetMemory(ctx, in.ID)
			if err != nil {
				return nil, memory.Memory{}, err
			}
			value = current.Confidence
			archivedAt = current.ArchivedAt
		} else {
			current, err := svc.GetMemory(ctx, in.ID)
			if err != nil {
				return nil, memory.Memory{}, err
			}
			archivedAt = current.ArchivedAt
		}
		m, e := svc.UpdateMemory(ctx, memory.Memory{ID: in.ID, CategoryID: in.CategoryID, Content: in.Content, Title: in.Title, Description: in.Description, Type: in.Type, Status: in.Status, GraphTier: in.GraphTier, Avoid: in.Avoid, Confidence: value, Source: in.Source, ArchivedAt: archivedAt})
		return nil, m, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_search", Description: "Search memories"}, func(ctx context.Context, _ *mcp.CallToolRequest, in searchInput) (*mcp.CallToolResult, []memory.Memory, error) {
		m, e := svc.Search(ctx, memory.MemoryFilter{ProjectID: in.ProjectID, KeyID: in.KeyID, CategoryID: in.CategoryID, Types: in.Types, Statuses: in.Statuses, GraphTiers: in.GraphTiers, Avoid: in.Avoid, MinConfidence: in.MinConfidence, Limit: in.Limit, Offset: in.Offset})
		return nil, m, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_archive", Description: "Archive a memory"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, memory.Memory, error) {
		m, e := svc.ArchiveMemory(ctx, memory.MemoryID(in.ID))
		return nil, m, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_restore", Description: "Restore a memory"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, memory.Memory, error) {
		m, e := svc.RestoreMemory(ctx, memory.MemoryID(in.ID))
		return nil, m, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_link", Description: "Create a memory relation"}, func(ctx context.Context, _ *mcp.CallToolRequest, in edgeInput) (*mcp.CallToolResult, memory.MemoryEdge, error) {
		e, x := svc.CreateEdge(ctx, memory.MemoryEdge{SourceID: in.SourceID, TargetID: in.TargetID, Relation: in.Relation, Confidence: confidence(in.Confidence), EvidenceStrength: in.EvidenceStrength, Direct: in.Direct, Source: in.Source})
		return nil, e, x
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_unlink", Description: "Delete a memory relation"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, struct{}, error) {
		return nil, struct{}{}, svc.DeleteEdge(ctx, memory.EdgeID(in.ID))
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_edges", Description: "List outgoing memory relations"}, func(ctx context.Context, _ *mcp.CallToolRequest, in idInput) (*mcp.CallToolResult, []memory.MemoryEdge, error) {
		e, x := svc.Outgoing(ctx, memory.MemoryID(in.ID))
		return nil, e, x
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_neighbors", Description: "Get memory neighbors"}, func(ctx context.Context, _ *mcp.CallToolRequest, in neighborInput) (*mcp.CallToolResult, memory.MemorySubgraph, error) {
		g, e := graph.Neighbors(ctx, in.ID, in.Depth, in.Relations, in.GraphTiers)
		return nil, g, e
	})
	mcp.AddTool(s, &mcp.Tool{Name: "memory_expand", Description: "Expand a memory subgraph"}, func(ctx context.Context, _ *mcp.CallToolRequest, in neighborInput) (*mcp.CallToolResult, memory.MemorySubgraph, error) {
		g, e := graph.Neighbors(ctx, in.ID, in.Depth, in.Relations, in.GraphTiers)
		return nil, g, e
	})
}
