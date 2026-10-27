package runtime

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"multihost-monitor/agent/internal/collector"
	"multihost-monitor/agent/internal/model"
	"multihost-monitor/agent/internal/queue"
	"multihost-monitor/agent/internal/transport"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type fakeCollector struct {
	calls  atomic.Int64
	warmup bool
}

func (c *fakeCollector) Collect(ctx context.Context, units []string) (model.Sample, error) {
	if c.warmup {
		return model.Sample{}, collector.ErrWarmup
	}
	return model.Sample{ID: fmt.Sprint(c.calls.Add(1)), At: time.Now().Unix()}, nil
}

type fakeSender struct {
	mu       sync.Mutex
	failures int
	code     int
	poison   bool
	accepted chan string
	calls    int
}

func (s *fakeSender) Config(context.Context) (model.RemoteConfig, error) {
	return model.RemoteConfig{HostID: 1, Interval: 30, MaxBatch: 32}, nil
}
func (s *fakeSender) Heartbeat(context.Context, int64, model.Metadata) error { return nil }
func (s *fakeSender) Metrics(ctx context.Context, b []model.Sample) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
	if s.code != 0 {
		return &transport.StatusError{Code: s.code}
	}
	if s.failures > 0 {
		s.failures--
		return errors.New("offline")
	}
	if s.poison {
		for _, v := range b {
			if v.ID == "bad" {
				return &transport.StatusError{Code: 422}
			}
		}
	}
	for _, v := range b {
		select {
		case s.accepted <- v.ID:
		default:
		}
	}
	return nil
}
func harness(sender *fakeSender, c *fakeCollector, q *queue.Queue) *Runtime {
	return &Runtime{Collector: c, Sender: sender, Queue: q, BatchSize: 3, Interval: 5 * time.Millisecond, Poll: time.Millisecond, Logger: slog.New(slog.NewJSONHandler(io.Discard, nil))}
}
func TestOfflineBufferAndRecovery(t *testing.T) {
	s := &fakeSender{failures: 1, accepted: make(chan string, 100)}
	q := queue.New(4)
	r := harness(s, &fakeCollector{}, q)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()
	select {
	case <-s.accepted:
	case <-time.After(5 * time.Second):
		t.Fatal("did not recover")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("did not stop")
	}
	_, dropped := q.Stats()
	if dropped == 0 {
		t.Fatal("bounded overflow not exercised")
	}
}
func TestPoisonBatchIsolation(t *testing.T) {
	s := &fakeSender{poison: true, accepted: make(chan string, 10)}
	q := queue.New(4)
	for _, id := range []string{"good1", "bad", "good2"} {
		q.Push(model.Sample{ID: id})
	}
	r := harness(s, &fakeCollector{warmup: true}, q)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()
	seen := map[string]bool{}
	for len(seen) < 2 {
		select {
		case id := <-s.accepted:
			seen[id] = true
		case <-time.After(time.Second):
			cancel()
			t.Fatal("valid samples blocked by poison")
		}
	}
	cancel()
	<-done
	if !seen["good1"] || !seen["good2"] || seen["bad"] {
		t.Fatal(seen)
	}
	if n, _ := q.Stats(); n != 0 {
		t.Fatal(n)
	}
}
func TestAuthenticationStopsAndJoinsCollector(t *testing.T) {
	s := &fakeSender{code: 401}
	q := queue.New(2)
	q.Push(model.Sample{ID: "a"})
	r := harness(s, &fakeCollector{warmup: true}, q)
	if err := r.Run(context.Background()); err == nil {
		t.Fatal("auth failure ignored")
	}
}
func TestBackoffBounds(t *testing.T) {
	for i := 0; i < 100; i++ {
		low, high := Backoff(i, 0), Backoff(i, 0.999)
		if low < time.Second || high > 300*time.Second || low > high {
			t.Fatal(i, low, high)
		}
	}
}
