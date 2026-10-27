package model

import (
	"crypto/rand"
	"fmt"
)

type Metadata struct {
	Hostname     string `json:"hostname"`
	OS           string `json:"os"`
	Arch         string `json:"arch"`
	AgentVersion string `json:"agent_version"`
}
type Disk struct {
	Mountpoint   string   `json:"mountpoint"`
	Total        uint64   `json:"total_bytes"`
	Used         uint64   `json:"used_bytes"`
	Available    uint64   `json:"available_bytes"`
	InodePercent *float64 `json:"inode_percent"`
}
type Network struct {
	Interface   string   `json:"interface"`
	Received    uint64   `json:"received_bytes"`
	Sent        uint64   `json:"sent_bytes"`
	ReceiveRate *float64 `json:"receive_rate"`
	SendRate    *float64 `json:"send_rate"`
}
type Service struct {
	Unit  string `json:"unit"`
	State string `json:"state"`
}
type Sample struct {
	ID              string    `json:"sample_id"`
	At              int64     `json:"sampled_at"`
	Metadata        Metadata  `json:"metadata"`
	CPU             float64   `json:"cpu_percent"`
	Load1           float64   `json:"load_1"`
	Load5           float64   `json:"load_5"`
	Load15          float64   `json:"load_15"`
	MemoryTotal     uint64    `json:"memory_total_bytes"`
	MemoryAvailable uint64    `json:"memory_available_bytes"`
	SwapTotal       uint64    `json:"swap_total_bytes"`
	SwapUsed        uint64    `json:"swap_used_bytes"`
	Uptime          uint64    `json:"uptime_seconds"`
	Disks           []Disk    `json:"disks"`
	Networks        []Network `json:"networks"`
	Services        []Service `json:"services"`
}
type Batch struct {
	HostID  int64    `json:"host_id"`
	Samples []Sample `json:"samples"`
}
type Heartbeat struct {
	HostID   int64    `json:"host_id"`
	At       int64    `json:"sampled_at"`
	Metadata Metadata `json:"metadata"`
}
type RemoteConfig struct {
	HostID   int64    `json:"host_id"`
	Interval int      `json:"interval_seconds"`
	MaxBatch int      `json:"max_batch"`
	Units    []string `json:"systemd_units"`
}

func NewID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16]), nil
}
