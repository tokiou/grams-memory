package memory

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"
)

const timeLayout = time.RFC3339Nano

func ts(t time.Time) string               { return t.UTC().Format(timeLayout) }
func parseTS(s string) (time.Time, error) { return time.Parse(timeLayout, s) }
func notFound(err error, domain error) error {
	if err == sql.ErrNoRows {
		return domain
	}
	return err
}
func archivedValue(t *time.Time) any {
	if t == nil {
		return nil
	}
	return ts(*t)
}

type ProjectRepository struct{ db *sql.DB }
type KeyRepository struct{ db *sql.DB }
type CategoryRepository struct{ db *sql.DB }
type MemoryRepository struct{ db *sql.DB }
type EdgeRepository struct{ db *sql.DB }

func NewProjectRepository(db *sql.DB) *ProjectRepository   { return &ProjectRepository{db} }
func NewKeyRepository(db *sql.DB) *KeyRepository           { return &KeyRepository{db} }
func NewCategoryRepository(db *sql.DB) *CategoryRepository { return &CategoryRepository{db} }
func NewMemoryRepository(db *sql.DB) *MemoryRepository     { return &MemoryRepository{db} }
func NewEdgeRepository(db *sql.DB) *EdgeRepository         { return &EdgeRepository{db} }

func (r *ProjectRepository) Create(ctx context.Context, p Project) error {
	_, e := r.db.ExecContext(ctx, "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)", p.ID, p.Name, p.Description, ts(p.CreatedAt), ts(p.UpdatedAt))
	return e
}
func (r *ProjectRepository) GetByID(ctx context.Context, id ProjectID) (*Project, error) {
	p := &Project{ID: id}
	var c, u string
	e := r.db.QueryRowContext(ctx, "SELECT name,description,created_at,updated_at FROM projects WHERE id=?", id).Scan(&p.Name, &p.Description, &c, &u)
	if e != nil {
		return nil, notFound(e, ErrProjectNotFound)
	}
	p.CreatedAt, e = parseTS(c)
	if e != nil {
		return nil, e
	}
	p.UpdatedAt, e = parseTS(u)
	return p, e
}
func (r *ProjectRepository) GetByName(ctx context.Context, n string) (*Project, error) {
	var id ProjectID
	e := r.db.QueryRowContext(ctx, "SELECT id FROM projects WHERE name=?", n).Scan(&id)
	if e != nil {
		return nil, notFound(e, ErrProjectNotFound)
	}
	return r.GetByID(ctx, id)
}
func (r *ProjectRepository) List(ctx context.Context) ([]Project, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT id,name,description,created_at,updated_at FROM projects ORDER BY name")
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var out []Project
	for rows.Next() {
		var p Project
		var c, u string
		if e = rows.Scan(&p.ID, &p.Name, &p.Description, &c, &u); e != nil {
			return nil, e
		}
		p.CreatedAt, e = parseTS(c)
		if e != nil {
			return nil, e
		}
		p.UpdatedAt, e = parseTS(u)
		if e != nil {
			return nil, e
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
func (r *ProjectRepository) Update(ctx context.Context, p Project) error {
	_, e := r.db.ExecContext(ctx, "UPDATE projects SET name=?,description=?,updated_at=? WHERE id=?", p.Name, p.Description, ts(p.UpdatedAt), p.ID)
	return e
}
func (r *ProjectRepository) Delete(ctx context.Context, id ProjectID) error {
	_, e := r.db.ExecContext(ctx, "DELETE FROM projects WHERE id=?", id)
	return e
}

func (r *KeyRepository) Create(ctx context.Context, k Key) error {
	_, e := r.db.ExecContext(ctx, "INSERT INTO keys(id,project_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)", k.ID, k.ProjectID, k.Name, k.Description, ts(k.CreatedAt), ts(k.UpdatedAt))
	return e
}
func (r *KeyRepository) GetByID(ctx context.Context, id KeyID) (*Key, error) {
	k := &Key{ID: id}
	var c, u string
	e := r.db.QueryRowContext(ctx, "SELECT project_id,name,description,created_at,updated_at FROM keys WHERE id=?", id).Scan(&k.ProjectID, &k.Name, &k.Description, &c, &u)
	if e != nil {
		return nil, notFound(e, ErrKeyNotFound)
	}
	k.CreatedAt, e = parseTS(c)
	if e != nil {
		return nil, e
	}
	k.UpdatedAt, e = parseTS(u)
	return k, e
}
func (r *KeyRepository) GetByName(ctx context.Context, p ProjectID, n string) (*Key, error) {
	var id KeyID
	e := r.db.QueryRowContext(ctx, "SELECT id FROM keys WHERE project_id=? AND name=?", p, n).Scan(&id)
	if e != nil {
		return nil, notFound(e, ErrKeyNotFound)
	}
	return r.GetByID(ctx, id)
}
func (r *KeyRepository) ListByProject(ctx context.Context, p ProjectID) ([]Key, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT id,name,description,created_at,updated_at FROM keys WHERE project_id=? ORDER BY name", p)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var out []Key
	for rows.Next() {
		var k Key
		var c, u string
		if e = rows.Scan(&k.ID, &k.Name, &k.Description, &c, &u); e != nil {
			return nil, e
		}
		k.ProjectID = p
		k.CreatedAt, e = parseTS(c)
		if e != nil {
			return nil, e
		}
		k.UpdatedAt, e = parseTS(u)
		if e != nil {
			return nil, e
		}
		out = append(out, k)
	}
	return out, rows.Err()
}
func (r *KeyRepository) Update(ctx context.Context, k Key) error {
	_, e := r.db.ExecContext(ctx, "UPDATE keys SET name=?,description=?,updated_at=? WHERE id=?", k.Name, k.Description, ts(k.UpdatedAt), k.ID)
	return e
}
func (r *KeyRepository) Delete(ctx context.Context, id KeyID) error {
	_, e := r.db.ExecContext(ctx, "DELETE FROM keys WHERE id=?", id)
	return e
}

func (r *CategoryRepository) Create(ctx context.Context, c Category) error {
	_, e := r.db.ExecContext(ctx, "INSERT INTO categories(id,key_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)", c.ID, c.KeyID, c.Name, c.Description, ts(c.CreatedAt), ts(c.UpdatedAt))
	return e
}
func (r *CategoryRepository) GetByID(ctx context.Context, id CategoryID) (*Category, error) {
	c := &Category{ID: id}
	var a, u string
	e := r.db.QueryRowContext(ctx, "SELECT key_id,name,description,created_at,updated_at FROM categories WHERE id=?", id).Scan(&c.KeyID, &c.Name, &c.Description, &a, &u)
	if e != nil {
		return nil, notFound(e, ErrCategoryNotFound)
	}
	c.CreatedAt, e = parseTS(a)
	if e != nil {
		return nil, e
	}
	c.UpdatedAt, e = parseTS(u)
	return c, e
}
func (r *CategoryRepository) GetByName(ctx context.Context, k KeyID, n string) (*Category, error) {
	var id CategoryID
	e := r.db.QueryRowContext(ctx, "SELECT id FROM categories WHERE key_id=? AND name=?", k, n).Scan(&id)
	if e != nil {
		return nil, notFound(e, ErrCategoryNotFound)
	}
	return r.GetByID(ctx, id)
}
func (r *CategoryRepository) ListByKey(ctx context.Context, k KeyID) ([]Category, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT id,name,description,created_at,updated_at FROM categories WHERE key_id=? ORDER BY name", k)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var out []Category
	for rows.Next() {
		var c Category
		var a, u string
		if e = rows.Scan(&c.ID, &c.Name, &c.Description, &a, &u); e != nil {
			return nil, e
		}
		c.KeyID = k
		c.CreatedAt, e = parseTS(a)
		if e != nil {
			return nil, e
		}
		c.UpdatedAt, e = parseTS(u)
		if e != nil {
			return nil, e
		}
		out = append(out, c)
	}
	return out, rows.Err()
}
func (r *CategoryRepository) Update(ctx context.Context, c Category) error {
	_, e := r.db.ExecContext(ctx, "UPDATE categories SET name=?,description=?,updated_at=? WHERE id=?", c.Name, c.Description, ts(c.UpdatedAt), c.ID)
	return e
}
func (r *CategoryRepository) Delete(ctx context.Context, id CategoryID) error {
	_, e := r.db.ExecContext(ctx, "DELETE FROM categories WHERE id=?", id)
	return e
}

func (r *MemoryRepository) Create(ctx context.Context, m Memory) error { return r.save(ctx, m, false) }
func (r *MemoryRepository) save(ctx context.Context, m Memory, update bool) error {
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var q string
	args := []any{m.CategoryID, m.Content, m.Title, m.Description, m.Type, m.Status, m.GraphTier, m.Confidence, m.Source, ts(m.UpdatedAt), archivedValue(m.ArchivedAt)}
	if update {
		q = "UPDATE memories SET category_id=?,content=?,title=?,description=?,type=?,status=?,graph_tier=?,confidence=?,source=?,updated_at=?,archived_at=? WHERE id=?"
		args = append(args, m.ID)
	} else {
		q = "INSERT INTO memories(id,category_id,content,title,description,type,status,graph_tier,confidence,source,created_at,updated_at,archived_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
		args = []any{m.ID, m.CategoryID, m.Content, m.Title, m.Description, m.Type, m.Status, m.GraphTier, m.Confidence, m.Source, ts(m.CreatedAt), ts(m.UpdatedAt), archivedValue(m.ArchivedAt)}
	}
	if _, e = tx.ExecContext(ctx, q, args...); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, "DELETE FROM memory_avoid WHERE memory_id=?", m.ID); e != nil {
		return e
	}
	for _, a := range m.Avoid {
		if _, e = tx.ExecContext(ctx, "INSERT INTO memory_avoid(memory_id,avoid_type) VALUES(?,?)", m.ID, a); e != nil {
			return e
		}
	}
	return tx.Commit()
}
func (r *MemoryRepository) Update(ctx context.Context, m Memory) error { return r.save(ctx, m, true) }
func (r *MemoryRepository) GetByID(ctx context.Context, id MemoryID) (*Memory, error) {
	m := &Memory{ID: id}
	var c, u string
	var archived sql.NullString
	e := r.db.QueryRowContext(ctx, "SELECT category_id,content,title,description,type,status,graph_tier,confidence,source,created_at,updated_at,archived_at FROM memories WHERE id=?", id).Scan(&m.CategoryID, &m.Content, &m.Title, &m.Description, &m.Type, &m.Status, &m.GraphTier, &m.Confidence, &m.Source, &c, &u, &archived)
	if e != nil {
		return nil, notFound(e, ErrMemoryNotFound)
	}
	m.CreatedAt, e = parseTS(c)
	if e != nil {
		return nil, e
	}
	m.UpdatedAt, e = parseTS(u)
	if archived.Valid {
		v, x := parseTS(archived.String)
		if x != nil {
			return nil, x
		}
		m.ArchivedAt = &v
	}
	rows, e := r.db.QueryContext(ctx, "SELECT avoid_type FROM memory_avoid WHERE memory_id=?", id)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	for rows.Next() {
		var a AvoidType
		if e = rows.Scan(&a); e != nil {
			return nil, e
		}
		m.Avoid = append(m.Avoid, a)
	}
	return m, rows.Err()
}
func (r *MemoryRepository) Delete(ctx context.Context, id MemoryID) error {
	_, e := r.db.ExecContext(ctx, "DELETE FROM memories WHERE id=?", id)
	return e
}
func (r *MemoryRepository) ListByCategory(ctx context.Context, id CategoryID) ([]Memory, error) {
	return r.Find(ctx, MemoryFilter{CategoryID: &id})
}
func (r *MemoryRepository) Find(ctx context.Context, f MemoryFilter) ([]Memory, error) {
	if f.Limit < 0 || f.Offset < 0 {
		return nil, ErrInvalidArgument
	}
	q := `SELECT m.id FROM memories m JOIN categories c ON c.id=m.category_id JOIN keys k ON k.id=c.key_id JOIN projects p ON p.id=k.project_id`
	var where []string
	var args []any
	if f.ProjectID != nil {
		where = append(where, "p.id=?")
		args = append(args, *f.ProjectID)
	}
	if f.KeyID != nil {
		where = append(where, "k.id=?")
		args = append(args, *f.KeyID)
	}
	if f.CategoryID != nil {
		where = append(where, "m.category_id=?")
		args = append(args, *f.CategoryID)
	}
	addIn := func(col string, vals []string) {
		if len(vals) == 0 {
			return
		}
		parts := make([]string, len(vals))
		for i, v := range vals {
			parts[i] = "?"
			args = append(args, v)
		}
		where = append(where, col+" IN ("+strings.Join(parts, ",")+")")
	}
	types := make([]string, len(f.Types))
	for i, v := range f.Types {
		types[i] = string(v)
	}
	addIn("m.type", types)
	statuses := make([]string, len(f.Statuses))
	for i, v := range f.Statuses {
		statuses[i] = string(v)
	}
	addIn("m.status", statuses)
	tiers := make([]string, len(f.GraphTiers))
	for i, v := range f.GraphTiers {
		tiers[i] = string(v)
	}
	addIn("m.graph_tier", tiers)
	if f.MinConfidence != nil {
		where = append(where, "m.confidence >= ?")
		args = append(args, *f.MinConfidence)
	}
	if len(f.Avoid) > 0 {
		parts := make([]string, len(f.Avoid))
		for i, v := range f.Avoid {
			parts[i] = "?"
			args = append(args, v)
		}
		where = append(where, "EXISTS (SELECT 1 FROM memory_avoid a WHERE a.memory_id=m.id AND a.avoid_type IN ("+strings.Join(parts, ",")+"))")
	}
	if len(where) > 0 {
		q += " WHERE " + strings.Join(where, " AND ")
	}
	q += " ORDER BY m.updated_at DESC"
	if f.Limit > 0 {
		q += fmt.Sprintf(" LIMIT %d", f.Limit)
	}
	if f.Offset > 0 {
		if f.Limit == 0 {
			q += " LIMIT -1"
		}
		q += fmt.Sprintf(" OFFSET %d", f.Offset)
	}
	rows, e := r.db.QueryContext(ctx, q, args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var ids []MemoryID
	for rows.Next() {
		var id MemoryID
		if e = rows.Scan(&id); e != nil {
			return nil, e
		}
		ids = append(ids, id)
	}
	if e = rows.Err(); e != nil {
		return nil, e
	}
	rows.Close()
	var out []Memory
	for _, id := range ids {
		m, e := r.GetByID(ctx, id)
		if e != nil {
			return nil, e
		}
		out = append(out, *m)
	}
	return out, nil
}

func (r *EdgeRepository) Create(ctx context.Context, e MemoryEdge) error {
	_, x := r.db.ExecContext(ctx, "INSERT INTO memory_edges(id,source_id,target_id,relation,confidence,evidence_strength,direct,source,created_at) VALUES(?,?,?,?,?,?,?,?,?)", e.ID, e.SourceID, e.TargetID, e.Relation, e.Confidence, e.EvidenceStrength, e.Direct, e.Source, ts(e.CreatedAt))
	return x
}
func (r *EdgeRepository) GetByID(ctx context.Context, id EdgeID) (*MemoryEdge, error) {
	e := &MemoryEdge{ID: id}
	var t string
	var d int
	x := r.db.QueryRowContext(ctx, "SELECT source_id,target_id,relation,confidence,evidence_strength,direct,source,created_at FROM memory_edges WHERE id=?", id).Scan(&e.SourceID, &e.TargetID, &e.Relation, &e.Confidence, &e.EvidenceStrength, &d, &e.Source, &t)
	if x != nil {
		return nil, notFound(x, ErrEdgeNotFound)
	}
	e.Direct = d != 0
	e.CreatedAt, x = parseTS(t)
	return e, x
}
func (r *EdgeRepository) Delete(ctx context.Context, id EdgeID) error {
	_, e := r.db.ExecContext(ctx, "DELETE FROM memory_edges WHERE id=?", id)
	return e
}
func (r *EdgeRepository) list(ctx context.Context, clause string, args ...any) ([]MemoryEdge, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT id,source_id,target_id,relation,confidence,evidence_strength,direct,source,created_at FROM memory_edges WHERE "+clause+" ORDER BY created_at", args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var out []MemoryEdge
	for rows.Next() {
		var x MemoryEdge
		var d int
		var t string
		if e = rows.Scan(&x.ID, &x.SourceID, &x.TargetID, &x.Relation, &x.Confidence, &x.EvidenceStrength, &d, &x.Source, &t); e != nil {
			return nil, e
		}
		x.Direct = d != 0
		x.CreatedAt, e = parseTS(t)
		if e != nil {
			return nil, e
		}
		out = append(out, x)
	}
	return out, rows.Err()
}
func (r *EdgeRepository) ListOutgoing(ctx context.Context, id MemoryID) ([]MemoryEdge, error) {
	return r.list(ctx, "source_id=?", id)
}
func (r *EdgeRepository) ListIncoming(ctx context.Context, id MemoryID) ([]MemoryEdge, error) {
	return r.list(ctx, "target_id=?", id)
}
func (r *EdgeRepository) ListBetween(ctx context.Context, a, b MemoryID) ([]MemoryEdge, error) {
	return r.list(ctx, "source_id=? AND target_id=?", a, b)
}
