package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func valid() Config {
	return Config{ServerURL: "https://monitor.test", HostID: 1, TokenFile: "/tmp/agent.token", Capacity: 240, BatchSize: 16, TimeoutSeconds: 10}
}
func TestConfigBoundaries(t *testing.T) {
	for _, change := range []func(*Config){func(c *Config) { c.ServerURL = "http://monitor.test" }, func(c *Config) { c.ServerURL = "https://user:password@monitor.test" }, func(c *Config) { c.Capacity = 0 }, func(c *Config) { c.BatchSize = 33 }, func(c *Config) { c.TokenFile = "relative" }, func(c *Config) { c.Units = []string{"x.service;id"} }, func(c *Config) { c.Mountpoints = []string{"/../etc"} }, func(c *Config) { c.Interfaces = []string{"eth0", "eth0"} }} {
		c := valid()
		change(&c)
		if c.Validate() == nil {
			t.Fatal("invalid config accepted")
		}
	}
	if valid().Validate() != nil {
		t.Fatal("valid config rejected")
	}
}
func TestPrivateToken(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.token")
	token := "mhm_" + strings.Repeat("x", 60)
	if err := os.WriteFile(path, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if got, err := ReadToken(path); err != nil || got != token {
		t.Fatal("token read failed")
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadToken(path); err == nil {
		t.Fatal("public token allowed")
	}
	os.Chmod(path, 0600)
	os.WriteFile(path, []byte("invalid"), 0600)
	if _, err := ReadToken(path); err == nil {
		t.Fatal("invalid token allowed")
	}
}
func TestStrictConfigJSON(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	for _, raw := range []string{`{"unknown":1}`, `{} {}`, `{"host_id":"1"}`} {
		os.WriteFile(path, []byte(raw), 0600)
		if _, err := Load(path); err == nil {
			t.Fatal("invalid JSON accepted")
		}
	}
}
