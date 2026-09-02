package memory

import "time"

type ProjectID string
type KeyID string
type CategoryID string
type MemoryID string
type EdgeID string

type Project struct {
	ID          ProjectID
	Name        string
	Description string

	CreatedAt time.Time
	UpdatedAt time.Time
}

type Key struct {
	ID        KeyID
	ProjectID ProjectID

	Name        string
	Description string

	CreatedAt time.Time
	UpdatedAt time.Time
}

type Category struct {
	ID    CategoryID
	KeyID KeyID

	Name        string
	Description string

	CreatedAt time.Time
	UpdatedAt time.Time
}

type MemoryType string

const (
	MemoryTypeFact        MemoryType = "FACT"
	MemoryTypeObservation MemoryType = "OBSERVATION"
	MemoryTypeHypothesis  MemoryType = "HYPOTHESIS"
	MemoryTypeConstraint  MemoryType = "CONSTRAINT"
	MemoryTypeAction      MemoryType = "ACTION"
	MemoryTypeError       MemoryType = "ERROR"
	MemoryTypeDecision    MemoryType = "DECISION"
	MemoryTypeState       MemoryType = "STATE"
	MemoryTypeResult      MemoryType = "RESULT"
)

type MemoryStatus string

const (
	MemoryStatusActive     MemoryStatus = "ACTIVE"
	MemoryStatusTentative  MemoryStatus = "TENTATIVE"
	MemoryStatusConfirmed  MemoryStatus = "CONFIRMED"
	MemoryStatusRejected   MemoryStatus = "REJECTED"
	MemoryStatusSuperseded MemoryStatus = "SUPERSEDED"
	MemoryStatusResolved   MemoryStatus = "RESOLVED"
	MemoryStatusFailed     MemoryStatus = "FAILED"
	MemoryStatusBlocked    MemoryStatus = "BLOCKED"
	MemoryStatusValidated  MemoryStatus = "VALIDATED"
)

type GraphTier string

const (
	GraphTierActive GraphTier = "ACTIVE"
	GraphTierCold   GraphTier = "COLD"
)

type AvoidType string

const (
	AvoidRepeat  AvoidType = "REPEAT"
	AvoidReread  AvoidType = "REREAD"
	AvoidRewrite AvoidType = "REWRITE"
	AvoidRethink AvoidType = "RETHINK"
)

type Memory struct {
	ID         MemoryID
	CategoryID CategoryID

	Content string

	Title       string
	Description string

	Type   MemoryType
	Status MemoryStatus

	GraphTier GraphTier

	Avoid []AvoidType

	// Expected range: 0.0 - 1.0
	Confidence float64

	// Examples: user, reasoning, tool_result, file_change.
	Source string

	CreatedAt  time.Time
	UpdatedAt  time.Time
	ArchivedAt *time.Time
}

type RelationType string

const (
	RelationSupports      RelationType = "SUPPORTS"
	RelationContradicts   RelationType = "CONTRADICTS"
	RelationTestedBy      RelationType = "TESTED_BY"
	RelationProduced      RelationType = "PRODUCED"
	RelationSucceededWith RelationType = "SUCCEEDED_WITH"
	RelationFailedBecause RelationType = "FAILED_BECAUSE"
	RelationBlockedBy     RelationType = "BLOCKED_BY"
	RelationDependsOn     RelationType = "DEPENDS_ON"
	RelationSupersedes    RelationType = "SUPERSEDES"
	RelationValidates     RelationType = "VALIDATES"
)

type EvidenceStrength string

const (
	EvidenceStrengthWeak   EvidenceStrength = "WEAK"
	EvidenceStrengthMedium EvidenceStrength = "MEDIUM"
	EvidenceStrengthStrong EvidenceStrength = "STRONG"
)

type MemoryEdge struct {
	ID EdgeID

	SourceID MemoryID
	TargetID MemoryID

	Relation RelationType

	// Expected range: 0.0 - 1.0
	Confidence float64

	EvidenceStrength EvidenceStrength

	// Direct = true means the relation comes directly from observed
	// evidence. false means GRAMS inferred the relation.
	Direct bool

	Source string

	CreatedAt time.Time
}

type MemorySubgraph struct {
	Nodes []Memory
	Edges []MemoryEdge
}
