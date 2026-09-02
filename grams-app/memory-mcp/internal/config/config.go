package config

import "os"

type Config struct {
	DatabasePath string
	Address      string
}

func Load() Config {
	path := os.Getenv("GRAMS_DB_PATH")
	if path == "" {
		path = "grams.db"
	}
	address := os.Getenv("GRAMS_MCP_ADDR")
	if address == "" {
		address = "127.0.0.1:8080"
	}
	return Config{DatabasePath: path, Address: address}
}
