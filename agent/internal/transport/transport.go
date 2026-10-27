package transport

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"multihost-monitor/agent/internal/config"
	"multihost-monitor/agent/internal/model"
)

type StatusError struct {
	Code       int
	RetryAfter time.Duration
}

func (e *StatusError) Error() string { return fmt.Sprintf("server status %d", e.Code) }
func Retryable(err error) bool {
	var status *StatusError
	if errors.As(err, &status) {
		return status.Code == 408 || status.Code == 429 || status.Code >= 500
	}
	return true
}
func Authentication(err error) bool {
	var status *StatusError
	return errors.As(err, &status) && (status.Code == 401 || status.Code == 403)
}

type Client struct {
	http      *http.Client
	origin    string
	tokenFile string
	hostID    int64
}

func New(c config.Config) (*Client, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	if _, err := config.ReadToken(c.TokenFile); err != nil {
		return nil, err
	}
	roots, err := x509.SystemCertPool()
	if err != nil {
		return nil, errors.New("system trust store unavailable")
	}
	if c.CAFile != "" {
		raw, e := os.ReadFile(c.CAFile)
		if e != nil || !roots.AppendCertsFromPEM(raw) {
			return nil, errors.New("invalid CA file")
		}
	}
	tr := &http.Transport{Proxy: http.ProxyFromEnvironment, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}, MaxIdleConns: 2, MaxConnsPerHost: 2, IdleConnTimeout: 60 * time.Second, ResponseHeaderTimeout: time.Duration(c.TimeoutSeconds) * time.Second}
	return &Client{http: &http.Client{Transport: tr, Timeout: time.Duration(c.TimeoutSeconds) * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, origin: strings.TrimRight(c.ServerURL, "/"), tokenFile: c.TokenFile, hostID: c.HostID}, nil
}
func (c *Client) Close() { c.http.CloseIdleConnections() }
func (c *Client) request(ctx context.Context, method, path string, body any, result any) error {
	var data []byte
	var err error
	if body != nil {
		data, err = json.Marshal(body)
		if err != nil || len(data) > 1024*1024 {
			return &StatusError{Code: 413}
		}
	}
	token, err := config.ReadToken(c.tokenFile)
	if err != nil {
		return &StatusError{Code: 401}
	}
	req, err := http.NewRequestWithContext(ctx, method, c.origin+path, bytes.NewReader(data))
	if err != nil {
		return errors.New("invalid request")
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(req)
	if err != nil {
		return errors.New("connection failed")
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil {
		return errors.New("response read failed")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		retry := time.Duration(0)
		var seconds int
		if _, e := fmt.Sscanf(response.Header.Get("Retry-After"), "%d", &seconds); e == nil && seconds > 0 {
			if seconds > 300 {
				seconds = 300
			}
			retry = time.Duration(seconds) * time.Second
		}
		return &StatusError{Code: response.StatusCode, RetryAfter: retry}
	}
	if len(raw) > 65536 {
		return errors.New("response too large")
	}
	if result != nil && json.Unmarshal(raw, result) != nil {
		return errors.New("invalid response")
	}
	return nil
}
func (c *Client) Config(ctx context.Context) (model.RemoteConfig, error) {
	var result model.RemoteConfig
	err := c.request(ctx, http.MethodGet, fmt.Sprintf("/api/v1/agent/config?host_id=%d", c.hostID), nil, &result)
	if err == nil {
		if result.HostID != c.hostID || result.Interval != 30 || result.MaxBatch < 1 || result.MaxBatch > 32 || len(result.Units) > 32 {
			return result, errors.New("invalid server configuration")
		}
		for _, s := range result.Units {
			if !config.UnitPattern.MatchString(s) {
				return result, errors.New("invalid service allowlist")
			}
		}
	}
	return result, err
}
func (c *Client) Metrics(ctx context.Context, samples []model.Sample) error {
	var result struct {
		Accepted   int `json:"accepted"`
		Duplicates int `json:"duplicates"`
	}
	err := c.request(ctx, http.MethodPost, "/api/v1/agent/metrics", model.Batch{HostID: c.hostID, Samples: samples}, &result)
	if err == nil && (result.Accepted < 0 || result.Duplicates < 0 || result.Accepted+result.Duplicates != len(samples)) {
		return errors.New("incomplete acknowledgement")
	}
	return err
}
func (c *Client) Heartbeat(ctx context.Context, at int64, metadata model.Metadata) error {
	return c.request(ctx, http.MethodPost, "/api/v1/agent/heartbeat", model.Heartbeat{HostID: c.hostID, At: at, Metadata: metadata}, nil)
}
