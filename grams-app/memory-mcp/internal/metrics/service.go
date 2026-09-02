package metrics

import "context"

type Service struct{ repository repository }

func NewService(repository repository) *Service { return &Service{repository: repository} }

func (s *Service) Snapshot(ctx context.Context, projectID string) (MetricsSnapshot, error) {
	return s.repository.Snapshot(ctx, projectID)
}

func (s *Service) Reconstruct(ctx context.Context, projectID string) (GraphSnapshot, error) {
	return s.repository.Reconstruct(ctx, projectID)
}
