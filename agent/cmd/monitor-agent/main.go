package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"multihost-monitor/agent/internal/collector"
	"multihost-monitor/agent/internal/config"
	"multihost-monitor/agent/internal/queue"
	agentruntime "multihost-monitor/agent/internal/runtime"
	"multihost-monitor/agent/internal/transport"
)

var version = "1.0.0-phase02"

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stderr, nil))
	slog.SetDefault(logger)
	path := flag.String("config", "/etc/multihost-monitor/config.json", "configuration file")
	showVersion := flag.Bool("version", false, "print version")
	flag.Parse()
	if *showVersion {
		logger.Info("agent_version", "version", version)
		return
	}
	c, err := config.Load(*path)
	if err != nil {
		logger.Error("configuration_rejected")
		os.Exit(1)
	}
	client, err := transport.New(c)
	if err != nil {
		logger.Error("transport_configuration_rejected")
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	r := agentruntime.Runtime{Collector: &collector.Collector{Config: c, Version: version}, Sender: client, Queue: queue.New(c.Capacity), BatchSize: c.BatchSize, Logger: logger}
	logger.Info("agent_started", "interval_seconds", 30, "queue_capacity", c.Capacity)
	err = r.Run(ctx)
	stop()
	client.Close()
	size, dropped := r.Queue.Stats()
	logger.Info("agent_stopped", "unflushed_samples", size, "dropped_total", dropped)
	if err != nil {
		logger.Error("agent_authentication_rejected")
		os.Exit(1)
	}
}
