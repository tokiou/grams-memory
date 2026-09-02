package metrics

import (
	"context"
	"database/sql"
	"fmt"
)

type Repository struct{ db *sql.DB }

func NewRepository(db *sql.DB) *Repository { return &Repository{db: db} }

type repository interface {
	Snapshot(context.Context, string) (MetricsSnapshot, error)
	Reconstruct(context.Context, string) (GraphSnapshot, error)
}

func scope(table string, projectID string) (string, []any) {
	if projectID == "" {
		if table == "memories" {
			return " WHERE 1 = 1", nil
		}
		return "", nil
	}
	if table == "projects" {
		return " WHERE id = ?", []any{projectID}
	}
	if table == "keys" {
		return " WHERE project_id = ?", []any{projectID}
	}
	if table == "categories" {
		return " WHERE key_id IN (SELECT id FROM keys WHERE project_id = ?)", []any{projectID}
	}
	return " WHERE category_id IN (SELECT c.id FROM categories c JOIN keys k ON k.id = c.key_id WHERE k.project_id = ?)", []any{projectID}
}

func (r *Repository) count(ctx context.Context, table, projectID string) (int, error) {
	where, args := scope(table, projectID)
	var count int
	if err := r.db.QueryRowContext(ctx, "SELECT COUNT(*) FROM "+table+where, args...).Scan(&count); err != nil {
		return 0, fmt.Errorf("count %s: %w", table, err)
	}
	return count, nil
}

func (r *Repository) breakdown(ctx context.Context, query string, args []any) ([]Breakdown, error) {
	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Breakdown
	for rows.Next() {
		var item Breakdown
		if err := rows.Scan(&item.Name, &item.Count); err != nil {
			return nil, err
		}
		out = append(out, item)
	}
	return out, rows.Err()
}

func (r *Repository) Snapshot(ctx context.Context, projectID string) (MetricsSnapshot, error) {
	var out MetricsSnapshot
	var err error
	for _, item := range []struct {
		name string
		dst  *int
	}{{"projects", &out.ProjectCount}, {"keys", &out.KeyCount}, {"categories", &out.CategoryCount}, {"memories", &out.MemoryCount}} {
		if *item.dst, err = r.count(ctx, item.name, projectID); err != nil {
			return out, err
		}
	}
	where, args := scope("memories", projectID)
	if out.EdgeCount, err = r.countEdges(ctx, projectID); err != nil {
		return out, err
	}
	if err = r.db.QueryRowContext(ctx, "SELECT COUNT(*) FROM memories"+where+" AND graph_tier = 'ACTIVE'", args...).Scan(&out.ActiveMemoryCount); err != nil {
		return out, err
	}
	if err = r.db.QueryRowContext(ctx, "SELECT COUNT(*) FROM memories"+where+" AND graph_tier = 'COLD'", args...).Scan(&out.ColdMemoryCount); err != nil {
		return out, err
	}
	if err = r.db.QueryRowContext(ctx, "SELECT COALESCE(AVG(confidence), 0) FROM memories"+where, args...).Scan(&out.AverageConfidence); err != nil {
		return out, err
	}
	if err = r.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM memories m`+where+` AND NOT EXISTS (SELECT 1 FROM memory_edges e WHERE e.source_id = m.id OR e.target_id = m.id)`, args...).Scan(&out.MemoriesWithoutEdges); err != nil {
		return out, err
	}
	if out.ProjectsByHierarchy, err = r.projects(ctx, projectID); err != nil {
		return out, err
	}
	if out.KeysByHierarchy, err = r.keys(ctx, projectID); err != nil {
		return out, err
	}
	if out.CategoriesByHierarchy, err = r.categories(ctx, projectID); err != nil {
		return out, err
	}
	if out.MemoryTypes, err = r.breakdown(ctx, `SELECT type, COUNT(*) FROM memories`+where+` GROUP BY type ORDER BY type`, args); err != nil {
		return out, err
	}
	if out.MemoryStatuses, err = r.breakdown(ctx, `SELECT status, COUNT(*) FROM memories`+where+` GROUP BY status ORDER BY status`, args); err != nil {
		return out, err
	}
	if out.MemoryTiers, err = r.breakdown(ctx, `SELECT graph_tier, COUNT(*) FROM memories`+where+` GROUP BY graph_tier ORDER BY graph_tier`, args); err != nil {
		return out, err
	}
	avoidWhere, avoidArgs := scope("memories", projectID)
	avoidQuery := `SELECT a.avoid_type, COUNT(*) FROM memory_avoid a JOIN memories m ON m.id = a.memory_id` + stringsReplaceMemoryWhere(avoidWhere) + ` GROUP BY a.avoid_type ORDER BY a.avoid_type`
	if out.AvoidTypes, err = r.breakdown(ctx, avoidQuery, avoidArgs); err != nil {
		return out, err
	}
	edgeWhere, edgeArgs := edgeScope(projectID)
	if out.EdgesByRelation, err = r.edgeBreakdown(ctx, edgeWhere, edgeArgs); err != nil {
		return out, err
	}
	if err = r.crossEdges(ctx, projectID, &out); err != nil {
		return out, err
	}
	return out, nil
}

func (r *Repository) Reconstruct(ctx context.Context, projectID string) (GraphSnapshot, error) {
	query := `SELECT p.id,p.name,p.description,k.id,k.name,k.description,c.id,c.name,c.description,m.id,m.content,m.title,m.description,m.type,m.status,m.graph_tier,m.confidence,m.source
		FROM projects p LEFT JOIN keys k ON k.project_id=p.id LEFT JOIN categories c ON c.key_id=k.id LEFT JOIN memories m ON m.category_id=c.id`
	var args []any
	if projectID != "" {
		query += " WHERE p.id = ?"
		args = append(args, projectID)
	}
	query += " ORDER BY p.name,k.name,c.name,m.updated_at"
	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return GraphSnapshot{}, err
	}
	defer rows.Close()
	var out GraphSnapshot
	projectIndex := map[string]int{}
	keyIndex := map[string]int{}
	categoryIndex := map[string]int{}
	for rows.Next() {
		var pid, pname, pdesc string
		var kid, kname, kdesc, cid, cname, cdesc sql.NullString
		var mid, mcontent, mtitle, mdescription, mtype, mstatus, mtier, msource sql.NullString
		var confidence sql.NullFloat64
		if err := rows.Scan(&pid, &pname, &pdesc, &kid, &kname, &kdesc, &cid, &cname, &cdesc, &mid, &mcontent, &mtitle, &mdescription, &mtype, &mstatus, &mtier, &confidence, &msource); err != nil {
			return out, err
		}
		pi, ok := projectIndex[pid]
		if !ok {
			pi = len(out.Projects)
			projectIndex[pid] = pi
			out.Projects = append(out.Projects, ProjectGraph{ID: pid, Name: pname, Description: pdesc})
		}
		if !kid.Valid {
			continue
		}
		key := pid + "\x00" + kid.String
		ki, ok := keyIndex[key]
		if !ok {
			ki = len(out.Projects[pi].Keys)
			keyIndex[key] = ki
			out.Projects[pi].Keys = append(out.Projects[pi].Keys, KeyGraph{ID: kid.String, Name: kname.String, Description: kdesc.String})
		}
		if !cid.Valid {
			continue
		}
		category := key + "\x00" + cid.String
		ci, ok := categoryIndex[category]
		if !ok {
			ci = len(out.Projects[pi].Keys[ki].Categories)
			categoryIndex[category] = ci
			out.Projects[pi].Keys[ki].Categories = append(out.Projects[pi].Keys[ki].Categories, CategoryGraph{ID: cid.String, Name: cname.String, Description: cdesc.String})
		}
		if mid.Valid {
			out.Projects[pi].Keys[ki].Categories[ci].Memories = append(out.Projects[pi].Keys[ki].Categories[ci].Memories, GraphMemory{ID: mid.String, Content: mcontent.String, Title: mtitle.String, Description: mdescription.String, Type: mtype.String, Status: mstatus.String, GraphTier: mtier.String, Confidence: confidence.Float64, Source: msource.String})
		}
	}
	if err := rows.Err(); err != nil {
		return out, err
	}
	edgeQuery := `SELECT e.id,e.source_id,e.target_id,e.relation,e.confidence,e.evidence_strength,e.direct,e.source
		FROM memory_edges e JOIN memories s ON s.id=e.source_id JOIN categories sc ON sc.id=s.category_id JOIN keys sk ON sk.id=sc.key_id
		JOIN memories t ON t.id=e.target_id JOIN categories tc ON tc.id=t.category_id JOIN keys tk ON tk.id=tc.key_id`
	args = nil
	if projectID != "" {
		edgeQuery += " WHERE sk.project_id = ? AND tk.project_id = ?"
		args = []any{projectID, projectID}
	}
	edges, err := r.db.QueryContext(ctx, edgeQuery, args...)
	if err != nil {
		return out, err
	}
	defer edges.Close()
	for edges.Next() {
		var edge GraphEdge
		var direct int
		if err := edges.Scan(&edge.ID, &edge.SourceID, &edge.TargetID, &edge.Relation, &edge.Confidence, &edge.EvidenceStrength, &direct, &edge.Source); err != nil {
			return out, err
		}
		edge.Direct = direct != 0
		if pi := projectForMemory(out, edge.SourceID); pi >= 0 {
			out.Projects[pi].Edges = append(out.Projects[pi].Edges, edge)
		}
	}
	return out, edges.Err()
}

func projectForMemory(snapshot GraphSnapshot, memoryID string) int {
	for i, project := range snapshot.Projects {
		for _, key := range project.Keys {
			for _, category := range key.Categories {
				for _, memory := range category.Memories {
					if memory.ID == memoryID {
						return i
					}
				}
			}
		}
	}
	return -1
}

func stringsReplaceMemoryWhere(where string) string {
	if where == "" {
		return ""
	}
	return " WHERE m.id IN (SELECT id FROM memories" + where + ")"
}
func (r *Repository) countEdges(ctx context.Context, projectID string) (int, error) {
	w, a := edgeScope(projectID)
	var n int
	err := r.db.QueryRowContext(ctx, "SELECT COUNT(*) FROM memory_edges"+w, a...).Scan(&n)
	return n, err
}
func edgeScope(projectID string) (string, []any) {
	if projectID == "" {
		return "", nil
	}
	const ids = "SELECT m.id FROM memories m JOIN categories c ON c.id=m.category_id JOIN keys k ON k.id=c.key_id WHERE k.project_id=?"
	return " WHERE source_id IN (" + ids + ") AND target_id IN (" + ids + ")", []any{projectID, projectID}
}
func (r *Repository) edgeBreakdown(ctx context.Context, w string, a []any) ([]EdgeMetrics, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT relation,COUNT(*) FROM memory_edges"+w+" GROUP BY relation ORDER BY relation", a...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var out []EdgeMetrics
	for rows.Next() {
		var x EdgeMetrics
		if e = rows.Scan(&x.Relation, &x.Count); e != nil {
			return nil, e
		}
		out = append(out, x)
	}
	return out, rows.Err()
}
func (r *Repository) projects(ctx context.Context, p string) ([]ProjectMetrics, error) {
	q := `SELECT p.id,p.name,COUNT(DISTINCT k.id),COUNT(DISTINCT c.id),COUNT(DISTINCT m.id) FROM projects p LEFT JOIN keys k ON k.project_id=p.id LEFT JOIN categories c ON c.key_id=k.id LEFT JOIN memories m ON m.category_id=c.id`
	var a []any
	if p != "" {
		q += " WHERE p.id=?"
		a = []any{p}
	}
	q += " GROUP BY p.id,p.name ORDER BY p.name"
	rows, e := r.db.QueryContext(ctx, q, a...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var o []ProjectMetrics
	for rows.Next() {
		var x ProjectMetrics
		if e = rows.Scan(&x.ProjectID, &x.ProjectName, &x.Keys, &x.Categories, &x.Memories); e != nil {
			return nil, e
		}
		o = append(o, x)
	}
	return o, rows.Err()
}
func (r *Repository) keys(ctx context.Context, p string) ([]KeyMetrics, error) {
	q := `SELECT k.id,k.name,k.project_id,COUNT(DISTINCT c.id),COUNT(DISTINCT m.id) FROM keys k LEFT JOIN categories c ON c.key_id=k.id LEFT JOIN memories m ON m.category_id=c.id`
	var a []any
	if p != "" {
		q += " WHERE k.project_id=?"
		a = []any{p}
	}
	q += " GROUP BY k.id,k.name,k.project_id ORDER BY k.name"
	rows, e := r.db.QueryContext(ctx, q, a...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var o []KeyMetrics
	for rows.Next() {
		var x KeyMetrics
		if e = rows.Scan(&x.KeyID, &x.KeyName, &x.ProjectID, &x.Categories, &x.Memories); e != nil {
			return nil, e
		}
		o = append(o, x)
	}
	return o, rows.Err()
}
func (r *Repository) categories(ctx context.Context, p string) ([]CategoryMetrics, error) {
	q := `SELECT c.id,c.name,c.key_id,COUNT(m.id) FROM categories c JOIN keys k ON k.id=c.key_id LEFT JOIN memories m ON m.category_id=c.id`
	var a []any
	if p != "" {
		q += " WHERE k.project_id=?"
		a = []any{p}
	}
	q += " GROUP BY c.id,c.name,c.key_id ORDER BY c.name"
	rows, e := r.db.QueryContext(ctx, q, a...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var o []CategoryMetrics
	for rows.Next() {
		var x CategoryMetrics
		if e = rows.Scan(&x.CategoryID, &x.CategoryName, &x.KeyID, &x.Memories); e != nil {
			return nil, e
		}
		o = append(o, x)
	}
	return o, rows.Err()
}
func (r *Repository) crossEdges(ctx context.Context, p string, out *MetricsSnapshot) error {
	w, a := edgeScope(p)
	q := `SELECT COUNT(*) FROM memory_edges e JOIN memories s ON s.id=e.source_id JOIN memories t ON t.id=e.target_id JOIN categories sc ON sc.id=s.category_id JOIN categories tc ON tc.id=t.category_id` + w
	if w != "" {
		q += " AND"
	} else {
		q += " WHERE"
	}
	q += " sc.id <> tc.id"
	if e := r.db.QueryRowContext(ctx, q, a...).Scan(&out.CrossCategoryEdges); e != nil {
		return e
	}
	q = `SELECT COUNT(*) FROM memory_edges e JOIN memories s ON s.id=e.source_id JOIN memories t ON t.id=e.target_id JOIN categories sc ON sc.id=s.category_id JOIN categories tc ON tc.id=t.category_id JOIN keys sk ON sk.id=sc.key_id JOIN keys tk ON tk.id=tc.key_id` + w
	if w != "" {
		q += " AND"
	} else {
		q += " WHERE"
	}
	q += " sk.id <> tk.id"
	return r.db.QueryRowContext(ctx, q, a...).Scan(&out.CrossKeyEdges)
}
