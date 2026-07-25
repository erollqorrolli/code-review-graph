"""Run a target's test suite under the call tracer, dump fired edges/nodes.

Usage: python runtime/trace/trace_repo.py <repo-name>
Runs with the target's own venv; adds the tool root to sys.path for CallTracer.
"""

import json
import os
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOL_ROOT))
from runtime.trace.call_tracer import CallTracer  # noqa: E402
from runtime.eval.mine_commits import TARGETS  # noqa: E402


def main():
    name = sys.argv[1]
    repo = (TOOL_ROOT / "evaluate/test_repos" / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    out = TOOL_ROOT / "runtime/trace" / f"{name}_trace.json"

    import pytest
    os.chdir(repo)
    tracer = CallTracer(pkg)
    tracer.start()
    try:
        code = pytest.main(["-q", "-m", "not network", "-p", "no:cacheprovider",
                            "--no-header", "-o", "addopts="])
    finally:
        tracer.stop()

    out.write_text(json.dumps({
        "pytest_exit": int(code),
        "fired_nodes": sorted(tracer.fired),
        "fired_edges": sorted(tracer.edges),
    }))
    print(f"\n{name}: fired nodes {len(tracer.fired)}  fired edges {len(tracer.edges)}  -> {out}")


if __name__ == "__main__":
    main()
