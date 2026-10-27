package queue

import (
	"fmt"
	"multihost-monitor/agent/internal/model"
	"sync"
	"testing"
)

func TestOverflowAndInflightAck(t *testing.T) {
	q := New(2)
	q.Push(model.Sample{ID: "a"})
	q.Push(model.Sample{ID: "b"})
	inflight := q.Peek(1)
	q.Push(model.Sample{ID: "c"})
	q.Ack([]string{inflight[0].ID})
	items := q.Peek(10)
	if len(items) != 2 || items[0].ID != "b" || items[1].ID != "c" {
		t.Fatal(items)
	}
	_, dropped := q.Stats()
	if dropped != 1 {
		t.Fatal(dropped)
	}
}
func TestConcurrentQueue(t *testing.T) {
	q := New(32)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				q.Push(model.Sample{ID: fmt.Sprintf("%d-%d", i, j)})
				items := q.Peek(4)
				if len(items) > 0 {
					q.Ack([]string{items[0].ID})
				}
				q.Stats()
			}
		}(i)
	}
	wg.Wait()
	n, _ := q.Stats()
	if n > 32 {
		t.Fatal(n)
	}
}
func BenchmarkQueue(b *testing.B) {
	q := New(240)
	s := model.Sample{ID: "id"}
	b.ReportAllocs()
	for b.Loop() {
		q.Push(s)
		q.Peek(16)
		q.Ack([]string{"id"})
	}
}
