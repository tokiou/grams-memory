package mcpserver

import (
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/mcp/tools"
	"github.com/tokiou/grams-memory/grams-app/memory_mcp/internal/memory"
)

func New(service *memory.Service, graph *memory.GraphService) *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "grams-memory", Version: "0.1.0"}, nil)
	tools.RegisterMemoryTools(s, service, graph)
	return s
}
