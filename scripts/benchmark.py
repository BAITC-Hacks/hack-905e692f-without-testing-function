"""Reproducible local runtime benchmark; prints only measurements from this run."""

from __future__ import annotations

import json
import resource
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core import auth
from app.core.data_pipeline import current_mart, waiting_source
from app.main import dataset, summary


def main() -> None:
    mart, metadata = current_mart()
    raw = waiting_source()
    raw_opens = 0
    original = Path.open

    def tracked(path: Path, *args, **kwargs):
        nonlocal raw_opens
        if path.resolve() == raw.resolve():
            raw_opens += 1
        return original(path, *args, **kwargs)

    Path.open = tracked
    issued = auth.issue("benchmark@local", role="viewer")
    latencies = []
    process = None
    try:
        dataset.cache_clear()
        summary()
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "--app-dir", "apps/api", "app.main:app", "--host", "127.0.0.1", "--port", "8766"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:8766/api/v1/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.25)
        for _ in range(12):
            request = urllib.request.Request("http://127.0.0.1:8766/api/v1/summary", headers={"Authorization": "Bearer " + issued["access_token"]})
            started = time.perf_counter()
            urllib.request.urlopen(request, timeout=30).read()
            latencies.append((time.perf_counter() - started) * 1000)
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        auth.revoke("Bearer " + issued["access_token"])
        Path.open = original
    steady = latencies[2:]
    output = {
        "dataset_version": metadata["dataset_version"],
        "raw_bytes": raw.stat().st_size,
        "processed_mart_bytes": mart.stat().st_size,
        "processed_to_raw_ratio": round(mart.stat().st_size / raw.stat().st_size, 4),
        "summary_requests": len(latencies),
        "summary_latency_ms_p50_warm": round(statistics.median(steady), 3),
        "summary_latency_ms_p95_warm": round(sorted(steady)[int(len(steady) * 0.95) - 1], 3),
        "raw_file_opens_during_requests": raw_opens,
        "process_max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
