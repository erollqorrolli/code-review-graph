"""Run httpx's test suite under the call tracer, dump fired edges/nodes.

Runs with httpx's own venv (its test deps), so we add the tool repo root to
sys.path just to import CallTracer.
"""

import json
import os
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOL_ROOT))
from runtime.trace.call_tracer import CallTracer  # noqa: E402

REPO = TOOL_ROOT / "evaluate/test_repos/httpx"
PKG = str((REPO / "httpx").resolve()) + "/"
OUT = TOOL_ROOT / "runtime/trace/httpx_trace.json"


def main():
    import pytest

    os.chdir(REPO)
    tracer = CallTracer(PKG)
    tracer.start()
    try:
        code = pytest.main(["-q", "-m", "not network", "-p", "no:cacheprovider",
                            "--no-header", "-o", "addopts="])
    finally:
        tracer.stop()

    OUT.write_text(json.dumps({
        "pytest_exit": int(code),
        "fired_nodes": sorted(tracer.fired),
        "fired_edges": sorted(tracer.edges),
    }))
    print(f"\nfired nodes: {len(tracer.fired)}  fired edges: {len(tracer.edges)}  -> {OUT}")


if __name__ == "__main__":
    main()
