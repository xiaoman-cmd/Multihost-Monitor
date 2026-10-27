package runtime

import (
	"context"
	"errors"
	"log/slog"
	"math/rand/v2"
	"sync"
	"time"

	"multihost-monitor/agent/internal/collector"
	"multihost-monitor/agent/internal/model"
	"multihost-monitor/agent/internal/queue"
	"multihost-monitor/agent/internal/transport"
)

type Collector interface {
	Collect(context.Context, []string) (model.Sample, error)
}
type Sender interface {
	Config(context.Context) (model.RemoteConfig, error)
	Metrics(context.Context, []model.Sample) error
	Heartbeat(context.Context, int64, model.Metadata) error
}
type Runtime struct {
	Collector Collector
	Sender    Sender
	Queue     *queue.Queue
	BatchSize int
	Logger    *slog.Logger
	Interval  time.Duration
	Poll      time.Duration
	mu        sync.RWMutex
	units     []string
	latest    *model.Sample
}

func Backoff(attempt int, random float64) time.Duration {
	if attempt < 0 {
		attempt = 0
	}
	if attempt > 8 {
		attempt = 8
	}
	base := time.Second * time.Duration(1<<attempt)
	if base > 150*time.Second {
		base = 150 * time.Second
	}
	return time.Duration(float64(base) * (1 + random))
}
func wait(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
func (r *Runtime) Run(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	if r.Interval == 0 {
		r.Interval = 30 * time.Second
	}
	if r.Poll == 0 {
		r.Poll = time.Second
	}
	if r.Logger == nil {
		r.Logger = slog.Default()
	}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); r.collect(ctx) }()
	defer func() { cancel(); wg.Wait() }()
	return r.send(ctx)
}
func (r *Runtime) collect(ctx context.Context) {
	ticker := time.NewTicker(r.Interval)
	defer ticker.Stop()
	for {
		r.mu.RLock()
		units := append([]string(nil), r.units...)
		r.mu.RUnlock()
		collectionStarted := time.Now()
		collectCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		sample, err := r.Collector.Collect(collectCtx, units)
		cancel()
		if err == nil {
			r.Queue.Push(sample)
			r.mu.Lock()
			copy := sample
			r.latest = &copy
			r.mu.Unlock()
			size, dropped := r.Queue.Stats()
			r.Logger.Info("sample_collected", "queued", size, "dropped_total", dropped,
				"duration_ms", time.Since(collectionStarted).Milliseconds())
		} else if !errors.Is(err, collector.ErrWarmup) && ctx.Err() == nil {
			r.Logger.Warn("collection_unavailable")
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
func (r *Runtime) send(ctx context.Context) error {
	attempt := 0
	single := false
	nextConfig := time.Time{}
	nextHeartbeat := time.Time{}
	for ctx.Err() == nil {
		if time.Now().After(nextConfig) {
			cfg, err := r.Sender.Config(ctx)
			if transport.Authentication(err) {
				return errors.New("agent authentication rejected")
			}
			if err == nil {
				r.mu.Lock()
				r.units = append([]string(nil), cfg.Units...)
				r.mu.Unlock()
				nextConfig = time.Now().Add(30 * time.Second)
			} else {
				nextConfig = time.Now().Add(30 * time.Second)
				r.Logger.Warn("config_refresh_unavailable")
			}
		}
		batchSize := r.BatchSize
		if single {
			batchSize = 1
		}
		batch := r.Queue.Peek(batchSize)
		var err error
		if len(batch) > 0 {
			uploadStarted := time.Now()
			err = r.Sender.Metrics(ctx, batch)
			if err == nil {
				ids := make([]string, len(batch))
				for i, s := range batch {
					ids[i] = s.ID
				}
				r.Queue.Ack(ids)
				attempt = 0
				single = false
				queued, dropped := r.Queue.Stats()
				r.Logger.Info("batch_accepted", "samples", len(batch), "queued", queued,
					"dropped_total", dropped, "duration_ms", time.Since(uploadStarted).Milliseconds())
			} else if transport.Authentication(err) {
				return errors.New("agent authentication rejected")
			} else if !transport.Retryable(err) {
				if len(batch) > 1 {
					single = true
					continue
				}
				r.Queue.Ack([]string{batch[0].ID})
				r.Logger.Warn("sample_rejected", "count", 1)
				single = false
				attempt = 0
				continue
			}
		} else if time.Now().After(nextHeartbeat) {
			r.mu.RLock()
			latest := r.latest
			r.mu.RUnlock()
			if latest != nil {
				err = r.Sender.Heartbeat(ctx, latest.At, latest.Metadata)
				if transport.Authentication(err) {
					return errors.New("agent authentication rejected")
				}
			}
			nextHeartbeat = time.Now().Add(30 * time.Second)
		}
		if err != nil {
			delay := Backoff(attempt, rand.Float64())
			var status *transport.StatusError
			if errors.As(err, &status) && status.RetryAfter > delay {
				delay = status.RetryAfter
			}
			attempt++
			r.Logger.Warn("upload_retry", "delay_ms", delay.Milliseconds())
			if !wait(ctx, delay) {
				break
			}
		} else {
			if !wait(ctx, r.Poll) {
				break
			}
		}
	}
	return nil
}
