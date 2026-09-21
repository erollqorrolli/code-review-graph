"""Trace an application-shaped workload instead of a test suite.

Writes to runtime/trace/<repo>_workload_trace.json in the same format as
trace_repo.py, so every downstream consumer works unchanged.
"""

import json
import os
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOL_ROOT))
from runtime.trace.call_tracer import CallTracer  # noqa: E402
from runtime.eval.mine_commits import TARGETS  # noqa: E402

WORKLOADS = {"httpx": "runtime.workload.httpx_workload"}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (TOOL_ROOT / "evaluate/test_repos" / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    out = TOOL_ROOT / "runtime/trace" / f"{name}_workload_trace.json"

    os.chdir(repo)
    mod = __import__(WORKLOADS[name], fromlist=["run"])
    tracer = CallTracer(pkg)
    tracer.start()
    try:
        summary = mod.run()
    finally:
        tracer.stop()

    out.write_text(json.dumps({
        "workload_summary": summary,
        "fired_nodes": sorted(tracer.fired),
        "fired_edges": sorted(tracer.edges),
        "fired_edge_counts": sorted([s, t, n] for (s, t), n in tracer.counts.items()),
    }))
    print(f"{name} workload: {summary}")
    print(f"fired nodes {len(tracer.fired)}  fired edges {len(tracer.edges)}  -> {out}")


if __name__ == "__main__":
    main()
