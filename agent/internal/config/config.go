package config

import (
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var UnitPattern = regexp.MustCompile(`^[A-Za-z0-9_:@.\-]{1,120}\.service$`)
var InterfacePattern = regexp.MustCompile(`^[A-Za-z0-9_.:@\-]{1,64}$`)
var TokenPattern = regexp.MustCompile(`^mhm_[A-Za-z0-9_-]{60}$`)

type Config struct {
	ServerURL      string   `json:"server_url"`
	HostID         int64    `json:"host_id"`
	TokenFile      string   `json:"token_file"`
	CAFile         string   `json:"ca_file"`
	Capacity       int      `json:"queue_capacity"`
	BatchSize      int      `json:"batch_size"`
	TimeoutSeconds int      `json:"timeout_seconds"`
	Mountpoints    []string `json:"mountpoints"`
	Interfaces     []string `json:"interfaces"`
	Units          []string `json:"systemd_units"`
}

func Load(path string) (Config, error) {
	c := Config{Capacity: 240, BatchSize: 16, TimeoutSeconds: 10}
	f, err := os.Open(path)
	if err != nil {
		return c, errors.New("cannot open configuration")
	}
	defer f.Close()
	decoder := json.NewDecoder(io.LimitReader(f, 65537))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&c) != nil {
		return c, errors.New("invalid configuration JSON")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return c, errors.New("configuration has trailing content")
	}
	return c, c.Validate()
}
func (c Config) Validate() error {
	u, err := url.Parse(c.ServerURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return errors.New("server_url must be an HTTPS origin")
	}
	if c.HostID <= 0 || !filepath.IsAbs(c.TokenFile) || c.Capacity < 1 || c.Capacity > 2048 || c.BatchSize < 1 || c.BatchSize > 32 || c.BatchSize > c.Capacity || c.TimeoutSeconds < 1 || c.TimeoutSeconds > 30 {
		return errors.New("invalid host, token path, buffer or timeout limits")
	}
	if len(c.Mountpoints) > 32 || len(c.Interfaces) > 32 || len(c.Units) > 32 {
		return errors.New("too many collection targets")
	}
	for _, m := range c.Mountpoints {
		if !filepath.IsAbs(m) || filepath.Clean(m) != m || len(m) > 512 {
			return errors.New("invalid mountpoint")
		}
	}
	for _, n := range c.Interfaces {
		if !InterfacePattern.MatchString(n) {
			return errors.New("invalid interface")
		}
	}
	for _, s := range c.Units {
		if !UnitPattern.MatchString(s) {
			return errors.New("invalid service unit")
		}
	}
	for _, list := range [][]string{c.Mountpoints, c.Interfaces, c.Units} {
		seen := map[string]bool{}
		for _, s := range list {
			if seen[s] {
				return errors.New("duplicate collection target")
			}
			seen[s] = true
		}
	}
	return nil
}
func ReadToken(path string) (string, error) {
	// Open once, then inspect the actual descriptor, preventing a path stat/read race.
	f, err := os.Open(path)
	if err != nil {
		return "", errors.New("cannot open token file")
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Mode().Perm()&0077 != 0 {
		return "", errors.New("token file must be a regular private file (0600 or 0400)")
	}
	raw, err := io.ReadAll(io.LimitReader(f, 129))
	if err != nil {
		return "", errors.New("cannot read token file")
	}
	token := strings.TrimSpace(string(raw))
	if !TokenPattern.MatchString(token) {
		return "", errors.New("invalid token file")
	}
	return token, nil
}
