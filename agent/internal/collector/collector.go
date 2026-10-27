package collector

import (
	"context"
	"errors"
	"math"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/load"
	"github.com/shirou/gopsutil/v4/mem"
	gnet "github.com/shirou/gopsutil/v4/net"
	"multihost-monitor/agent/internal/config"
	"multihost-monitor/agent/internal/model"
)

var ErrWarmup = errors.New("sampling baseline established")

type Counter struct{ Received, Sent uint64 }
type NetworkBaseline struct {
	Counters map[string]Counter
	At       time.Time
}

func Rates(previous NetworkBaseline, current map[string]Counter, at time.Time) ([]model.Network, NetworkBaseline) {
	result := make([]model.Network, 0, len(current))
	seconds := at.Sub(previous.At).Seconds()
	for name, c := range current {
		n := model.Network{Interface: name, Received: c.Received, Sent: c.Sent}
		p, ok := previous.Counters[name]
		if ok && seconds > 0 && c.Received >= p.Received && c.Sent >= p.Sent {
			rx := float64(c.Received-p.Received) / seconds
			tx := float64(c.Sent-p.Sent) / seconds
			n.ReceiveRate = &rx
			n.SendRate = &tx
		}
		result = append(result, n)
	}
	return result, NetworkBaseline{Counters: current, At: at}
}
func CPUPercent(previous, current cpu.TimesStat) (float64, bool) {
	// Guest values are already included in user/nice on Linux.
	total := func(t cpu.TimesStat) float64 {
		return t.User + t.System + t.Nice + t.Idle + t.Iowait + t.Irq + t.Softirq + t.Steal
	}
	elapsed := total(current) - total(previous)
	idle := current.Idle + current.Iowait - previous.Idle - previous.Iowait
	if elapsed <= 0 || idle < 0 || idle > elapsed {
		return 0, false
	}
	return math.Max(0, math.Min(100, 100*(elapsed-idle)/elapsed)), true
}

type Collector struct {
	Config      config.Config
	Version     string
	previousCPU *cpu.TimesStat
	network     NetworkBaseline
}

func (c *Collector) Collect(ctx context.Context, units []string) (model.Sample, error) {
	s := model.Sample{Disks: []model.Disk{}, Networks: []model.Network{}, Services: []model.Service{}}
	times, err := cpu.TimesWithContext(ctx, false)
	if err != nil || len(times) != 1 {
		return s, errors.New("CPU collection unavailable")
	}
	counters, netErr := gnet.IOCountersWithContext(ctx, true)
	current := map[string]Counter{}
	if netErr == nil {
		for _, n := range counters {
			if config.InterfacePattern.MatchString(n.Name) && allowed(c.Config.Interfaces, n.Name) && len(current) < 32 {
				current[n.Name] = Counter{n.BytesRecv, n.BytesSent}
			}
		}
	}
	at := time.Now()
	s.Networks, c.network = Rates(c.network, current, at)
	previous := c.previousCPU
	c.previousCPU = &times[0]
	if previous == nil {
		return s, ErrWarmup
	}
	var valid bool
	s.CPU, valid = CPUPercent(*previous, times[0])
	if !valid {
		return s, ErrWarmup
	}
	info, err := host.InfoWithContext(ctx)
	if err != nil {
		return s, errors.New("host metadata unavailable")
	}
	vm, err := mem.VirtualMemoryWithContext(ctx)
	if err != nil || vm.Total == 0 {
		return s, errors.New("memory collection unavailable")
	}
	swap, err := mem.SwapMemoryWithContext(ctx)
	if err != nil {
		return s, errors.New("swap collection unavailable")
	}
	avg, err := load.AvgWithContext(ctx)
	if err != nil {
		return s, errors.New("load collection unavailable")
	}
	s.ID, err = model.NewID()
	if err != nil {
		return s, errors.New("sample ID unavailable")
	}
	s.At = at.Unix()
	s.Metadata = model.Metadata{Hostname: info.Hostname, OS: info.OS + "/" + info.Platform + "/" + info.PlatformVersion, Arch: runtime.GOARCH, AgentVersion: c.Version}
	s.MemoryTotal = vm.Total
	s.MemoryAvailable = vm.Available
	s.SwapTotal = swap.Total
	s.SwapUsed = swap.Used
	s.Uptime = info.Uptime
	s.Load1 = avg.Load1
	s.Load5 = avg.Load5
	s.Load15 = avg.Load15
	mounts := c.Config.Mountpoints
	if len(mounts) == 0 {
		partitions, e := disk.PartitionsWithContext(ctx, false)
		if e == nil {
			seen := map[string]bool{}
			for _, p := range partitions {
				if !seen[p.Mountpoint] && len(mounts) < 32 {
					mounts = append(mounts, p.Mountpoint)
					seen[p.Mountpoint] = true
				}
			}
		}
	}
	for _, m := range mounts {
		usage, e := disk.UsageWithContext(ctx, m)
		if e != nil || usage.Total == 0 {
			continue
		}
		d := model.Disk{Mountpoint: m, Total: usage.Total, Used: usage.Used, Available: usage.Free}
		if usage.InodesTotal > 0 {
			percent := usage.InodesUsedPercent
			d.InodePercent = &percent
		}
		s.Disks = append(s.Disks, d)
	}
	// Only locally configured units intersected with the remote allowlist are inspected.
	for _, unit := range units {
		if config.UnitPattern.MatchString(unit) && contains(c.Config.Units, unit) {
			s.Services = append(s.Services, model.Service{Unit: unit, State: ServiceState(ctx, unit)})
		}
	}
	return s, nil
}
func allowed(list []string, name string) bool { return len(list) == 0 || contains(list, name) }
func contains(list []string, name string) bool {
	for _, s := range list {
		if s == name {
			return true
		}
	}
	return false
}
func ServiceState(ctx context.Context, unit string) string {
	if !config.UnitPattern.MatchString(unit) || runtime.GOOS != "linux" {
		return "unavailable"
	}
	// A fixed executable and fixed arguments. Never invoke a shell or use command data from the server.
	path := "/usr/bin/systemctl"
	if _, err := exec.LookPath(path); err != nil {
		return "unavailable"
	}
	commandCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	output, err := exec.CommandContext(commandCtx, path, "show", "--property=ActiveState", "--value", "--", unit).Output()
	if err != nil {
		return "unavailable"
	}
	state := strings.TrimSpace(string(output))
	switch state {
	case "active", "inactive", "failed", "activating", "deactivating", "reloading":
		return state
	default:
		return "unknown"
	}
}
