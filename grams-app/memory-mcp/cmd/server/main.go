package main

import (
	"context"
	"log"
	"net/http"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/tokiou/grams-memory/grams-app/memory-mcp/internal/app"
	"github.com/tokiou/grams-memory/grams-app/memory-mcp/internal/config"
)

func main() {
	ctx := context.Background()
	a, err := app.New(ctx, config.Load())
	if err != nil {
		log.Fatal(err)
	}
	defer a.Close()
	handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return a.Server }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	log.Printf("GRAMS Memory MCP listening on %s", config.Load().Address)
	log.Fatal(http.ListenAndServe(config.Load().Address, handler))
}
