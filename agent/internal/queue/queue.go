// Package queue implements bounded at-least-once buffering. The oldest waiting
// sample is evicted on overflow; in-flight snapshots are immutable and acknowledged by ID.
package queue

import (
	"multihost-monitor/agent/internal/model"
	"sync"
)

type Queue struct {
	mu       sync.Mutex
	capacity int
	items    []model.Sample
	dropped  uint64
}

func New(capacity int) *Queue {
	if capacity < 1 {
		panic("queue capacity must be positive")
	}
	return &Queue{capacity: capacity}
}
func (q *Queue) Push(s model.Sample) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if len(q.items) == q.capacity {
		copy(q.items, q.items[1:])
		q.items = q.items[:len(q.items)-1]
		q.dropped++
	}
	q.items = append(q.items, s)
}
func (q *Queue) Peek(n int) []model.Sample {
	q.mu.Lock()
	defer q.mu.Unlock()
	if n > len(q.items) {
		n = len(q.items)
	}
	return append([]model.Sample(nil), q.items[:n]...)
}
func (q *Queue) Ack(ids []string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	remove := map[string]bool{}
	for _, id := range ids {
		remove[id] = true
	}
	kept := q.items[:0]
	for _, s := range q.items {
		if !remove[s.ID] {
			kept = append(kept, s)
		}
	}
	clear(q.items[len(kept):])
	q.items = kept
}
func (q *Queue) Stats() (int, uint64) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.items), q.dropped
}
