package metrics

type Breakdown struct {
	Name  string `json:"name"`
	Count int    `json:"count"`
}

type ProjectMetrics struct {
	ProjectID   string `json:"project_id"`
	ProjectName string `json:"project_name"`
	Keys        int    `json:"keys"`
	Categories  int    `json:"categories"`
	Memories    int    `json:"memories"`
}

type KeyMetrics struct {
	KeyID      string `json:"key_id"`
	KeyName    string `json:"key_name"`
	ProjectID  string `json:"project_id"`
	Categories int    `json:"categories"`
	Memories   int    `json:"memories"`
}

type CategoryMetrics struct {
	CategoryID   string `json:"category_id"`
	CategoryName string `json:"category_name"`
	KeyID        string `json:"key_id"`
	Memories     int    `json:"memories"`
}

type EdgeMetrics struct {
	Relation string `json:"relation"`
	Count    int    `json:"count"`
}

type MetricsSnapshot struct {
	ProjectCount  int `json:"project_count"`
	KeyCount      int `json:"key_count"`
	CategoryCount int `json:"category_count"`
	MemoryCount   int `json:"memory_count"`
	EdgeCount     int `json:"edge_count"`

	ActiveMemoryCount    int     `json:"active_memory_count"`
	ColdMemoryCount      int     `json:"cold_memory_count"`
	AverageConfidence    float64 `json:"average_confidence"`
	MemoriesWithoutEdges int     `json:"memories_without_edges"`
	CrossCategoryEdges   int     `json:"cross_category_edges"`
	CrossKeyEdges        int     `json:"cross_key_edges"`

	ProjectsByHierarchy   []ProjectMetrics  `json:"projects_by_hierarchy"`
	KeysByHierarchy       []KeyMetrics      `json:"keys_by_hierarchy"`
	CategoriesByHierarchy []CategoryMetrics `json:"categories_by_hierarchy"`
	MemoryTypes           []Breakdown       `json:"memory_types"`
	MemoryStatuses        []Breakdown       `json:"memory_statuses"`
	MemoryTiers           []Breakdown       `json:"memory_tiers"`
	AvoidTypes            []Breakdown       `json:"avoid_types"`
	EdgesByRelation       []EdgeMetrics     `json:"edges_by_relation"`
}

// GraphSnapshot is the complete user-facing hierarchy. Edges live at project
// level because they may connect memories from different categories or keys.
type GraphSnapshot struct {
	Projects []ProjectGraph `json:"projects"`
}

type ProjectGraph struct {
	ID          string      `json:"id"`
	Name        string      `json:"name"`
	Description string      `json:"description"`
	Keys        []KeyGraph  `json:"keys"`
	Edges       []GraphEdge `json:"edges"`
}

type KeyGraph struct {
	ID          string          `json:"id"`
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Categories  []CategoryGraph `json:"categories"`
}

type CategoryGraph struct {
	ID          string        `json:"id"`
	Name        string        `json:"name"`
	Description string        `json:"description"`
	Memories    []GraphMemory `json:"memories"`
}

type GraphMemory struct {
	ID          string  `json:"id"`
	Content     string  `json:"content"`
	Title       string  `json:"title"`
	Description string  `json:"description"`
	Type        string  `json:"type"`
	Status      string  `json:"status"`
	GraphTier   string  `json:"graph_tier"`
	Confidence  float64 `json:"confidence"`
	Source      string  `json:"source"`
}

type GraphEdge struct {
	ID               string  `json:"id"`
	SourceID         string  `json:"source_id"`
	TargetID         string  `json:"target_id"`
	Relation         string  `json:"relation"`
	Confidence       float64 `json:"confidence"`
	EvidenceStrength string  `json:"evidence_strength"`
	Direct           bool    `json:"direct"`
	Source           string  `json:"source"`
}
