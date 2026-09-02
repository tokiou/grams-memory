package sqlite

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/tokiou/grams-memory/grams-app/memory-mcp/migrations"
)

func Migrate(ctx context.Context, db *sql.DB) error {
	if _, err := db.ExecContext(ctx, migrations.Initial); err != nil {
		return fmt.Errorf("apply migrations: %w", err)
	}
	return nil
}
