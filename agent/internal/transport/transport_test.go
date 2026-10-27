package transport

import (
	"context"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"multihost-monitor/agent/internal/config"
	"multihost-monitor/agent/internal/model"
)

func fixture(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server) {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	t.Cleanup(server.Close)
	dir := t.TempDir()
	token := filepath.Join(dir, "agent.token")
	ca := filepath.Join(dir, "ca.pem")
	os.WriteFile(token, []byte("mhm_"+strings.Repeat("x", 60)), 0600)
	os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600)
	c, err := New(config.Config{ServerURL: server.URL, HostID: 1, TokenFile: token, CAFile: ca, Capacity: 10, BatchSize: 2, TimeoutSeconds: 1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.Close)
	return c, server
}
func TestTLSBearerAndAcknowledgement(t *testing.T) {
	c, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer mhm_"+strings.Repeat("x", 60) {
			t.Error("missing bearer")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"accepted":1,"duplicates":0}`))
	})
	if err := c.Metrics(context.Background(), []model.Sample{{ID: "id"}}); err != nil {
		t.Fatal(err)
	}
}
func TestOfflineRecoveryAndStatusClassification(t *testing.T) {
	var calls atomic.Int32
	c, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.Header().Set("Retry-After", "5")
			w.WriteHeader(503)
			return
		}
		w.Write([]byte(`{"accepted":1,"duplicates":0}`))
	})
	err := c.Metrics(context.Background(), []model.Sample{{ID: "id"}})
	if !Retryable(err) {
		t.Fatal("503 not retried")
	}
	var status *StatusError
	if !errors.As(err, &status) || status.RetryAfter != 5*time.Second {
		t.Fatal(err)
	}
	if c.Metrics(context.Background(), []model.Sample{{ID: "id"}}) != nil {
		t.Fatal("recovery failed")
	}
	for _, code := range []int{400, 401, 403, 409, 413, 422} {
		if Retryable(&StatusError{Code: code}) {
			t.Fatal(code)
		}
	}
	for _, code := range []int{408, 429, 500, 503} {
		if !Retryable(&StatusError{Code: code}) {
			t.Fatal(code)
		}
	}
}
func TestRefuseUntrustedTLS(t *testing.T) {
	c, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{}`)) })
	c.http.Transport.(*http.Transport).TLSClientConfig.RootCAs = x509.NewCertPool()
	if c.Heartbeat(context.Background(), 1, model.Metadata{}) == nil {
		t.Fatal("untrusted TLS accepted")
	}
}
func TestNoRedirectCredentialForwarding(t *testing.T) {
	var leaked atomic.Bool
	target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked.Store(true) }))
	defer target.Close()
	c, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	})
	err := c.Heartbeat(context.Background(), 1, model.Metadata{})
	if Retryable(err) || leaked.Load() {
		t.Fatal("redirect followed")
	}
}
func TestConnectionLossCancellationAndSafeErrors(t *testing.T) {
	c, server := fixture(t, func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := c.Heartbeat(ctx, 1, model.Metadata{})
	if err == nil || strings.Contains(err.Error(), "mhm_") {
		t.Fatal("unsafe failure")
	}
	server.Close()
	if !Retryable(c.Heartbeat(context.Background(), 1, model.Metadata{})) {
		t.Fatal("offline not retryable")
	}
}
func TestInvalidAcknowledgementRetried(t *testing.T) {
	c, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"accepted":0,"duplicates":0}`)) })
	if err := c.Metrics(context.Background(), []model.Sample{{}}); err == nil || !Retryable(err) {
		t.Fatal("false ack")
	}
}
