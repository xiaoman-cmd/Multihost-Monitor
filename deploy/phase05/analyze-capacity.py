#!/usr/bin/env python3
"""Summarize the read-only SSH series; fail if the 30-minute gate is unmet."""
import json
import re
import statistics
import sys


def summarize(path):
    samples = []
    current = None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("BEGIN "):
            _, stamp, epoch = line.split()
            current = {"time_utc": stamp, "epoch": int(epoch), "processes": []}
        elif line == "END":
            if current is None or not all(k in current for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "cpu")):
                raise ValueError("incomplete sample")
            samples.append(current)
            current = None
        elif current is not None:
            memory = re.match(r"^(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+)", line)
            process = re.match(r"^(\d+)\s+(\S+)\s+(\d+)\s+([\d.]+)$", line)
            if memory:
                current[memory[1]] = int(memory[2]) * 1024
            elif line.startswith("cpu "):
                current["cpu"] = [int(x) for x in line.split()[1:]]
            elif process:
                current["processes"].append({"pid": int(process[1]), "name": process[2],
                                             "rss_bytes": int(process[3]) * 1024,
                                             "ps_cpu_percent": float(process[4])})
    if current is not None or len(samples) < 61 or samples[-1]["epoch"] - samples[0]["epoch"] < 1800:
        raise ValueError("30-minute / 61-sample gate not met")
    series = []
    peaks = {}
    for index, sample in enumerate(samples):
        cpu_busy = None
        if index:
            before, after = samples[index - 1]["cpu"], sample["cpu"]
            total = sum(after[:8]) - sum(before[:8])
            idle = after[3] + after[4] - before[3] - before[4]
            cpu_busy = round(100 * (total - idle) / total, 2) if total > 0 else None
        series.append({"time_utc": sample["time_utc"], "available_bytes": sample["MemAvailable"],
                       "swap_used_bytes": sample["SwapTotal"] - sample["SwapFree"],
                       "host_cpu_busy_percent": cpu_busy})
        for process in sample["processes"]:
            old = peaks.get(process["name"])
            if old is None or process["rss_bytes"] > old["rss_bytes"]:
                peaks[process["name"]] = process
    available = [s["available_bytes"] for s in series]
    busy = [s["host_cpu_busy_percent"] for s in series if s["host_cpu_busy_percent"] is not None]
    return {"source": path, "samples": len(samples), "first_utc": samples[0]["time_utc"],
            "last_utc": samples[-1]["time_utc"], "duration_seconds": samples[-1]["epoch"] - samples[0]["epoch"],
            "memory_total_bytes": samples[0]["MemTotal"], "available_min_bytes": min(available),
            "available_median_bytes": int(statistics.median(available)),
            "swap_used_first_bytes": series[0]["swap_used_bytes"],
            "swap_used_last_bytes": series[-1]["swap_used_bytes"],
            "host_cpu_busy_max_percent": max(busy),
            "host_cpu_busy_p95_percent": sorted(busy)[int(0.95 * (len(busy) - 1))],
            "top_process_peak_rss": sorted(peaks.values(), key=lambda p: p["rss_bytes"], reverse=True)[:12],
            "series": series}


if __name__ == "__main__":
    print(json.dumps([summarize(path) for path in sys.argv[1:]], ensure_ascii=False, indent=2))
