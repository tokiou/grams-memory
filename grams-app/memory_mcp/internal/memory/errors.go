package memory

import "errors"

var (
	ErrProjectNotFound       = errors.New("project not found")
	ErrKeyNotFound           = errors.New("key not found")
	ErrCategoryNotFound      = errors.New("category not found")
	ErrMemoryNotFound        = errors.New("memory not found")
	ErrEdgeNotFound          = errors.New("edge not found")
	ErrInvalidConfidence     = errors.New("confidence must be between 0 and 1")
	ErrCrossProjectEdge      = errors.New("memory edges cannot cross projects")
	ErrMemoryAlreadyArchived = errors.New("memory already archived")
	ErrInvalidArgument       = errors.New("invalid argument")
)

type MemoryFilter struct {
	ProjectID     *ProjectID
	KeyID         *KeyID
	CategoryID    *CategoryID
	Types         []MemoryType
	Statuses      []MemoryStatus
	GraphTiers    []GraphTier
	Avoid         []AvoidType
	MinConfidence *float64
	Limit, Offset int
}
