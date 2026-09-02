package memory

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

type projectRepository interface {
	Create(context.Context, Project) error
	GetByID(context.Context, ProjectID) (*Project, error)
	GetByName(context.Context, string) (*Project, error)
	List(context.Context) ([]Project, error)
}
type keyRepository interface {
	Create(context.Context, Key) error
	GetByID(context.Context, KeyID) (*Key, error)
	GetByName(context.Context, ProjectID, string) (*Key, error)
	ListByProject(context.Context, ProjectID) ([]Key, error)
}
type categoryRepository interface {
	Create(context.Context, Category) error
	GetByID(context.Context, CategoryID) (*Category, error)
	GetByName(context.Context, KeyID, string) (*Category, error)
	ListByKey(context.Context, KeyID) ([]Category, error)
}
type memoryRepository interface {
	Create(context.Context, Memory) error
	GetByID(context.Context, MemoryID) (*Memory, error)
	Update(context.Context, Memory) error
	Delete(context.Context, MemoryID) error
	Find(context.Context, MemoryFilter) ([]Memory, error)
}
type edgeRepository interface {
	Create(context.Context, MemoryEdge) error
	GetByID(context.Context, EdgeID) (*MemoryEdge, error)
	Delete(context.Context, EdgeID) error
	ListOutgoing(context.Context, MemoryID) ([]MemoryEdge, error)
	ListIncoming(context.Context, MemoryID) ([]MemoryEdge, error)
	ListBetween(context.Context, MemoryID, MemoryID) ([]MemoryEdge, error)
}

type Service struct {
	projects   projectRepository
	keys       keyRepository
	categories categoryRepository
	memories   memoryRepository
	edges      edgeRepository
}

func NewService(p projectRepository, k keyRepository, c categoryRepository, m memoryRepository, e edgeRepository) *Service {
	return &Service{p, k, c, m, e}
}
func newID() string { return uuid.NewString() }
func validateName(s string) error {
	if strings.TrimSpace(s) == "" {
		return fmt.Errorf("%w: name is required", ErrInvalidArgument)
	}
	return nil
}
func nowPair() (time.Time, time.Time) { n := time.Now().UTC(); return n, n }

func (s *Service) CreateProject(ctx context.Context, p Project) (Project, error) {
	if e := validateName(p.Name); e != nil {
		return p, e
	}
	if p.ID == "" {
		p.ID = ProjectID(newID())
	}
	p.CreatedAt, p.UpdatedAt = nowPair()
	if e := s.projects.Create(ctx, p); e != nil {
		return p, e
	}
	return p, nil
}
func (s *Service) GetProject(ctx context.Context, id ProjectID) (*Project, error) {
	return s.projects.GetByID(ctx, id)
}
func (s *Service) ListProjects(ctx context.Context) ([]Project, error) { return s.projects.List(ctx) }
func (s *Service) CreateKey(ctx context.Context, k Key) (Key, error) {
	if e := validateName(k.Name); e != nil {
		return k, e
	}
	if _, e := s.projects.GetByID(ctx, k.ProjectID); e != nil {
		return k, e
	}
	if k.ID == "" {
		k.ID = KeyID(newID())
	}
	k.CreatedAt, k.UpdatedAt = nowPair()
	if e := s.keys.Create(ctx, k); e != nil {
		return k, e
	}
	return k, nil
}
func (s *Service) GetKey(ctx context.Context, id KeyID) (*Key, error) { return s.keys.GetByID(ctx, id) }
func (s *Service) ListKeys(ctx context.Context, p ProjectID) ([]Key, error) {
	return s.keys.ListByProject(ctx, p)
}
func (s *Service) CreateCategory(ctx context.Context, c Category) (Category, error) {
	if e := validateName(c.Name); e != nil {
		return c, e
	}
	if _, e := s.keys.GetByID(ctx, c.KeyID); e != nil {
		return c, e
	}
	if c.ID == "" {
		c.ID = CategoryID(newID())
	}
	c.CreatedAt, c.UpdatedAt = nowPair()
	if e := s.categories.Create(ctx, c); e != nil {
		return c, e
	}
	return c, nil
}
func (s *Service) GetCategory(ctx context.Context, id CategoryID) (*Category, error) {
	return s.categories.GetByID(ctx, id)
}
func (s *Service) ListCategories(ctx context.Context, k KeyID) ([]Category, error) {
	return s.categories.ListByKey(ctx, k)
}

func validMemoryType(v MemoryType) bool {
	switch v {
	case MemoryTypeFact, MemoryTypeObservation, MemoryTypeHypothesis, MemoryTypeConstraint, MemoryTypeAction, MemoryTypeError, MemoryTypeDecision, MemoryTypeState, MemoryTypeResult:
		return true
	}
	return false
}
func validStatus(v MemoryStatus) bool {
	switch v {
	case MemoryStatusActive, MemoryStatusTentative, MemoryStatusConfirmed, MemoryStatusRejected, MemoryStatusSuperseded, MemoryStatusResolved, MemoryStatusFailed, MemoryStatusBlocked, MemoryStatusValidated:
		return true
	}
	return false
}
func validTier(v GraphTier) bool { return v == GraphTierActive || v == GraphTierCold }
func validAvoid(v AvoidType) bool {
	return v == AvoidRepeat || v == AvoidReread || v == AvoidRewrite || v == AvoidRethink
}
func validRelation(v RelationType) bool {
	switch v {
	case RelationSupports, RelationContradicts, RelationTestedBy, RelationProduced, RelationSucceededWith, RelationFailedBecause, RelationBlockedBy, RelationDependsOn, RelationSupersedes, RelationValidates:
		return true
	}
	return false
}
func validEvidence(v EvidenceStrength) bool {
	return v == EvidenceStrengthWeak || v == EvidenceStrengthMedium || v == EvidenceStrengthStrong
}
func (s *Service) validateMemory(m Memory) error {
	if strings.TrimSpace(m.Content) == "" {
		return fmt.Errorf("%w: content is required", ErrInvalidArgument)
	}
	if m.Confidence < 0 || m.Confidence > 1 {
		return ErrInvalidConfidence
	}
	if !validMemoryType(m.Type) || !validStatus(m.Status) || !validTier(m.GraphTier) {
		return fmt.Errorf("%w: invalid memory enum", ErrInvalidArgument)
	}
	for _, a := range m.Avoid {
		if !validAvoid(a) {
			return fmt.Errorf("%w: invalid avoid type", ErrInvalidArgument)
		}
	}
	return nil
}
func (s *Service) CreateMemory(ctx context.Context, m Memory) (Memory, error) {
	if m.Type == "" {
		m.Type = MemoryTypeFact
	}
	if m.GraphTier == "" {
		m.GraphTier = GraphTierActive
	}
	if m.Status == "" {
		m.Status = MemoryStatusActive
	}
	if e := s.validateMemory(m); e != nil {
		return m, e
	}
	if _, e := s.categories.GetByID(ctx, m.CategoryID); e != nil {
		return m, e
	}
	if m.ID == "" {
		m.ID = MemoryID(newID())
	}
	m.CreatedAt, m.UpdatedAt = nowPair()
	if e := s.memories.Create(ctx, m); e != nil {
		return m, e
	}
	return m, nil
}
func (s *Service) GetMemory(ctx context.Context, id MemoryID) (*Memory, error) {
	return s.memories.GetByID(ctx, id)
}
func (s *Service) UpdateMemory(ctx context.Context, m Memory) (Memory, error) {
	old, e := s.memories.GetByID(ctx, m.ID)
	if e != nil {
		return m, e
	}
	if m.ArchivedAt == nil && m.GraphTier == GraphTierCold {
		m.ArchivedAt = old.ArchivedAt
	}
	if m.CategoryID == "" {
		m.CategoryID = old.CategoryID
	}
	if m.Type == "" {
		m.Type = old.Type
	}
	if m.Status == "" {
		m.Status = old.Status
	}
	if m.GraphTier == "" {
		m.GraphTier = old.GraphTier
	}
	if m.Content == "" {
		m.Content = old.Content
	}
	if m.Title == "" {
		m.Title = old.Title
	}
	if m.Description == "" {
		m.Description = old.Description
	}
	if m.Source == "" {
		m.Source = old.Source
	}
	if m.Avoid == nil {
		m.Avoid = old.Avoid
	}
	if m.CreatedAt.IsZero() {
		m.CreatedAt = old.CreatedAt
	}
	if e = s.validateMemory(m); e != nil {
		return m, e
	}
	m.UpdatedAt = time.Now().UTC()
	if e = s.memories.Update(ctx, m); e != nil {
		return m, e
	}
	return m, nil
}
func (s *Service) ArchiveMemory(ctx context.Context, id MemoryID) (Memory, error) {
	m, e := s.memories.GetByID(ctx, id)
	if e != nil {
		return Memory{}, e
	}
	if m.GraphTier == GraphTierCold {
		return *m, ErrMemoryAlreadyArchived
	}
	n := time.Now().UTC()
	m.GraphTier = GraphTierCold
	m.ArchivedAt = &n
	m.UpdatedAt = n
	e = s.memories.Update(ctx, *m)
	return *m, e
}
func (s *Service) RestoreMemory(ctx context.Context, id MemoryID) (Memory, error) {
	m, e := s.memories.GetByID(ctx, id)
	if e != nil {
		return Memory{}, e
	}
	m.GraphTier = GraphTierActive
	m.ArchivedAt = nil
	m.UpdatedAt = time.Now().UTC()
	e = s.memories.Update(ctx, *m)
	return *m, e
}
func (s *Service) Search(ctx context.Context, f MemoryFilter) ([]Memory, error) {
	return s.memories.Find(ctx, f)
}
func (s *Service) CreateEdge(ctx context.Context, e MemoryEdge) (MemoryEdge, error) {
	a, x := s.memories.GetByID(ctx, e.SourceID)
	if x != nil {
		return e, x
	}
	b, x := s.memories.GetByID(ctx, e.TargetID)
	if x != nil {
		return e, x
	}
	ca, x := s.categories.GetByID(ctx, a.CategoryID)
	if x != nil {
		return e, x
	}
	cb, x := s.categories.GetByID(ctx, b.CategoryID)
	if x != nil {
		return e, x
	}
	ka, x := s.keys.GetByID(ctx, ca.KeyID)
	if x != nil {
		return e, x
	}
	kb, x := s.keys.GetByID(ctx, cb.KeyID)
	if x != nil {
		return e, x
	}
	pa, x := s.projects.GetByID(ctx, ka.ProjectID)
	if x != nil {
		return e, x
	}
	pb, x := s.projects.GetByID(ctx, kb.ProjectID)
	if x != nil {
		return e, x
	}
	_ = pa
	if pb.ID != pa.ID {
		return e, ErrCrossProjectEdge
	}
	if e.SourceID == e.TargetID {
		return e, fmt.Errorf("%w: self edge", ErrInvalidArgument)
	}
	if e.Confidence < 0 || e.Confidence > 1 {
		return e, ErrInvalidConfidence
	}
	if e.ID == "" {
		e.ID = EdgeID(newID())
	}
	if e.CreatedAt.IsZero() {
		e.CreatedAt = time.Now().UTC()
	}
	if e.EvidenceStrength == "" {
		e.EvidenceStrength = EvidenceStrengthMedium
	}
	if e.Relation == "" {
		return e, fmt.Errorf("%w: relation is required", ErrInvalidArgument)
	}
	if !validRelation(e.Relation) || !validEvidence(e.EvidenceStrength) {
		return e, fmt.Errorf("%w: invalid edge enum", ErrInvalidArgument)
	}
	if x = s.edges.Create(ctx, e); x != nil {
		return e, x
	}
	return e, nil
}
func (s *Service) GetEdge(ctx context.Context, id EdgeID) (*MemoryEdge, error) {
	return s.edges.GetByID(ctx, id)
}
func (s *Service) DeleteEdge(ctx context.Context, id EdgeID) error { return s.edges.Delete(ctx, id) }
func (s *Service) Outgoing(ctx context.Context, id MemoryID) ([]MemoryEdge, error) {
	return s.edges.ListOutgoing(ctx, id)
}
func (s *Service) Incoming(ctx context.Context, id MemoryID) ([]MemoryEdge, error) {
	return s.edges.ListIncoming(ctx, id)
}
