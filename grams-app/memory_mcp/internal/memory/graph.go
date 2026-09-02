package memory

import "context"

type GraphService struct {
	memories memoryRepository
	edges    edgeRepository
}

func NewGraphService(m memoryRepository, e edgeRepository) *GraphService { return &GraphService{m, e} }

func (g *GraphService) Neighbors(ctx context.Context, id MemoryID, depth int, relations []RelationType, tiers []GraphTier) (MemorySubgraph, error) {
	if depth < 1 {
		depth = 1
	}
	if depth > 10 {
		return MemorySubgraph{}, ErrInvalidArgument
	}
	seen := map[MemoryID]bool{id: true}
	edgeSeen := map[EdgeID]bool{}
	front := []MemoryID{id}
	var result MemorySubgraph
	start, e := g.memories.GetByID(ctx, id)
	if e != nil {
		return result, e
	}
	result.Nodes = append(result.Nodes, *start)
	for level := 0; level < depth && len(front) > 0; level++ {
		var next []MemoryID
		for _, cur := range front {
			out, e := g.edges.ListOutgoing(ctx, cur)
			if e != nil {
				return result, e
			}
			in, e := g.edges.ListIncoming(ctx, cur)
			if e != nil {
				return result, e
			}
			all := append(out, in...)
			for _, edge := range all {
				if edgeSeen[edge.ID] {
					continue
				}
				edgeSeen[edge.ID] = true
				allowed := len(relations) == 0
				for _, r := range relations {
					if edge.Relation == r {
						allowed = true
					}
				}
				if !allowed {
					continue
				}
				nid := edge.SourceID
				if nid == cur {
					nid = edge.TargetID
				}
				if !seen[nid] {
					m, e := g.memories.GetByID(ctx, nid)
					if e != nil {
						return result, e
					}
					if len(tiers) > 0 {
						ok := false
						for _, t := range tiers {
							if m.GraphTier == t {
								ok = true
							}
						}
						if !ok {
							continue
						}
					}
					seen[nid] = true
					result.Nodes = append(result.Nodes, *m)
					next = append(next, nid)
				}
				result.Edges = append(result.Edges, edge)
			}
		}
		front = next
	}
	return result, nil
}
