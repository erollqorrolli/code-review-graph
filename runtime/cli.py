"""Command line for the runtime layer.

    python -m runtime record <repo> [trace.json]   persist observed edges
    python -m runtime augment <repo>               merge them into the graph
    python -m runtime status <repo>                what is recorded/materialized
    python -m runtime clear <repo>                 drop materialized edges

``record`` reads a dump from runtime/trace/<repo>_trace.json by default; produce
one with ``runtime/trace/trace_repo.py <repo>`` using the target's own venv.
"""

from __future__ import annotations

import sys
from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path

from runtime.augment import (
    RUNTIME_TIER, clear, ensure_schema, load_trace, materialize, record_observations,
)
from runtime.eval.mine_commits import TARGETS


def _open(name: str):
    repo = (Path("evaluate/test_repos") / name).resolve()
    if not repo.exists():
        repo = Path(name).resolve()
    return repo, GraphStore(get_db_path(repo))


def _pkg_prefix(name: str, repo: Path) -> str:
    if name in TARGETS:
        return str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    return str(repo) + "/"


def cmd_record(name: str, trace_path: str | None) -> None:
    repo, store = _open(name)
    path = Path(trace_path) if trace_path else Path(f"runtime/trace/{name}_trace.json")
    edges = load_trace(path, _pkg_prefix(name, repo))
    n = record_observations(store, edges, run_id=path.stem)
    print(f"recorded {n} observed edges from {path}")
    store.close()


def cmd_augment(name: str) -> None:
    _, store = _open(name)
    print(materialize(store))
    store.close()


def cmd_status(name: str) -> None:
    _, store = _open(name)
    ensure_schema(store)
    obs = store._conn.execute(
        "SELECT COUNT(DISTINCT source_qualified || target_qualified) FROM runtime_edges"
    ).fetchone()[0]
    mat = store._conn.execute(
        "SELECT COUNT(*) FROM edges WHERE confidence_tier = ?", (RUNTIME_TIER,)
    ).fetchone()[0]
    static = store._conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind = 'CALLS' AND confidence_tier != ?",
        (RUNTIME_TIER,),
    ).fetchone()[0]
    runs = [r[0] for r in store._conn.execute("SELECT DISTINCT run_id FROM runtime_edges")]
    print(f"observed edges recorded : {obs}")
    print(f"materialized in graph   : {mat}")
    print(f"static CALLS edges      : {static}")
    print(f"runs                    : {', '.join(runs) or '-'}")
    store.close()


def cmd_clear(name: str) -> None:
    _, store = _open(name)
    print(f"removed {clear(store)} materialized runtime edges")
    store.close()


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    cmd, name = sys.argv[1], sys.argv[2]
    if cmd == "record":
        cmd_record(name, sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "augment":
        cmd_augment(name)
    elif cmd == "status":
        cmd_status(name)
    elif cmd == "clear":
        cmd_clear(name)
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
