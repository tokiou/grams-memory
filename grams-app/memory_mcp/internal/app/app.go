package app

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/config"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/mcp"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/memory"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/platform/sqlite"
)

type App struct {
	DB     *sql.DB
	Server *mcp.Server
}

func New(ctx context.Context, cfg config.Config) (*App, error) {
	db, err := sqlite.New(ctx, cfg.DatabasePath)
	if err != nil {
		return nil, err
	}
	if err = sqlite.Migrate(ctx, db); err != nil {
		db.Close()
		return nil, err
	}
	pr := memory.NewProjectRepository(db)
	kr := memory.NewKeyRepository(db)
	cr := memory.NewCategoryRepository(db)
	mr := memory.NewMemoryRepository(db)
	er := memory.NewEdgeRepository(db)
	svc := memory.NewService(pr, kr, cr, mr, er)
	graph := memory.NewGraphService(mr, er)
	return &App{DB: db, Server: mcpserver.New(svc, graph)}, nil
}

func (a *App) Close() error {
	if a == nil || a.DB == nil {
		return nil
	}
	if err := a.DB.Close(); err != nil {
		return fmt.Errorf("close database: %w", err)
	}
	return nil
}
