package collector

import (
	"context"
	"github.com/shirou/gopsutil/v4/cpu"
	"testing"
	"time"
)

func TestCPUSamplingAndRollback(t *testing.T) {
	p := cpu.TimesStat{User: 10, Idle: 90}
	if _, ok := CPUPercent(p, p); ok {
		t.Fatal("zero interval accepted")
	}
	value, ok := CPUPercent(p, cpu.TimesStat{User: 20, Idle: 100})
	if !ok || value != 50 {
		t.Fatal(value, ok)
	}
	if _, ok = CPUPercent(p, cpu.TimesStat{Idle: 1}); ok {
		t.Fatal("rollback accepted")
	}
}
func TestNetworkNewResetRemovedAndChanged(t *testing.T) {
	at := time.Now()
	first, baseline := Rates(NetworkBaseline{}, map[string]Counter{"eth0": {100, 200}}, at)
	if first[0].ReceiveRate != nil {
		t.Fatal("initial rate fabricated")
	}
	next, baseline := Rates(baseline, map[string]Counter{"eth0": {200, 400}}, at.Add(10*time.Second))
	if *next[0].ReceiveRate != 10 || *next[0].SendRate != 20 {
		t.Fatal(next)
	}
	reset, baseline := Rates(baseline, map[string]Counter{"eth0": {1, 2}}, at.Add(20*time.Second))
	if reset[0].ReceiveRate != nil || reset[0].SendRate != nil {
		t.Fatal("counter rollback rate")
	}
	changed, baseline := Rates(baseline, map[string]Counter{"eth1": {20, 40}}, at.Add(30*time.Second))
	if changed[0].ReceiveRate != nil || len(baseline.Counters) != 1 {
		t.Fatal("interface change")
	}
	_, baseline = Rates(baseline, map[string]Counter{}, at.Add(40*time.Second))
	if len(baseline.Counters) != 0 {
		t.Fatal("removed interface retained")
	}
}
func TestUnsafeServiceUnavailable(t *testing.T) {
	if ServiceState(context.Background(), "sshd.service;id") != "unavailable" {
		t.Fatal("invalid unit accepted")
	}
}

// This is a real local-OS collection smoke test, NOT Linux host acceptance.
func TestLocalCollectionWarmupAndInaccessibleDisk(t *testing.T) {
	c := &Collector{Version: "1.0.0-phase02"}
	c.Config.Mountpoints = []string{"/multihost-does-not-exist-test"}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := c.Collect(ctx, nil); err != ErrWarmup {
		t.Fatal("first CPU baseline must not emit a sample", err)
	}
	time.Sleep(100 * time.Millisecond)
	sample, err := c.Collect(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	if sample.MemoryTotal == 0 || sample.CPU < 0 || sample.CPU > 100 || sample.Metadata.Hostname == "" || sample.ID == "" {
		t.Fatal("invalid local telemetry")
	}
	if len(sample.Disks) != 0 {
		t.Fatal("inaccessible disk should be skipped")
	}
}
