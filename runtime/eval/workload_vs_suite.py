"""Does the ranking gain survive when the runtime signal is app usage?

Every earlier result derives its edges from the target's own test suite, which
is written for coverage rather than representativeness -- the single biggest
caveat on this work. This re-runs the ranking evaluation with edges recovered
from an application-shaped workload instead, and reports both side by side.

Pre-registered: workload-derived edges clearing >= +15% MRR over static (the
same bar the suite-derived result had to clear) means the finding is not an
artefact of exhaustive testing.
"""

import shutil
import sys
from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path
from runtime.augment import (
    clear, ensure_schema, load_trace, materialize, record_observations,
)
from runtime.eval.mine_commits import TARGETS
from runtime.eval.augmented_eval import seeds_for
from runtime.eval.ranking_union import union_rr, summarize


def variant(repo: Path, pkg: str, seeds, trace: Path | None, run_id: str):
    tmp = get_db_path(repo).parent / "wl_tmp.db"
    shutil.copy(get_db_path(repo), tmp)
    store = GraphStore(tmp)
    clear(store)
    # clear() drops materialized edges but not the observations behind them;
    # without this each variant inherits whatever was recorded earlier and
    # every run reports the same union.
    ensure_schema(store)
    store._conn.execute("DELETE FROM runtime_edges")
    store._conn.commit()
    info = ""
    if trace is not None:
        record_observations(store, load_trace(trace, pkg), run_id=run_id)
        info = str(materialize(store))
    m = summarize(union_rr(store, seeds))
    store.close()
    tmp.unlink(missing_ok=True)
    return m, info


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    seeds = seeds_for(name, repo)
    td = Path("runtime/trace")

    print(f"=== {name}: test suite vs application workload (n={len(seeds)}) ===")
    base, _ = variant(repo, pkg, seeds, None, "")
    print(f"{'static':>10}: {base}")

    out = {}
    for label, trace in (("suite", td / f"{name}_trace.json"),
                         ("workload", td / f"{name}_workload_trace.json")):
        m, info = variant(repo, pkg, seeds, trace, f"{name}_{label}")
        out[label] = m
        rel = 100 * (m["MRR"] - base["MRR"]) / base["MRR"] if base["MRR"] else 0.0
        print(f"{label:>10}: {m}   MRR {rel:+.1f}% vs static   [{info}]")

    print("\npre-registered: workload >= +15% MRR over static means the finding")
    print("is not an artefact of exhaustive test-suite coverage.")


if __name__ == "__main__":
    main()
