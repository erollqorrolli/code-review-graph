"""Does execution *frequency* add signal beyond execution *presence*?

The augmented result treats every observed edge alike: it fired, so it is
real. This asks the untested half of the original thesis -- whether weighting
an edge by how often it fired improves ranking further.

Baseline here is the AUGMENTED graph, not the static one; the question is
whether frequency adds anything on top of presence.

Pre-registered: >= +10% relative union-MRR and >= +3pp hit@3 over augmented
counts as real. Weight function fixed in advance (log-scaled onto
[MIN_WEIGHT, 1.0]), not swept.

Counter-hypothesis worth stating: frequency measures how hot a path is, not
how likely a file is to be co-edited. A utility called 48,000 times is stable
and may be *less* likely to be co-changed than one called twice.
"""

import shutil
import sys
from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path
from runtime.augment import clear, load_trace, materialize, record_observations
from runtime.eval.mine_commits import TARGETS
from runtime.eval.augmented_eval import seeds_for
from runtime.eval.ranking_union import union_rr, summarize


def variant(repo: Path, name: str, pkg: str, seeds, mode: str):
    """mode: 'static' | 'uniform' | 'weighted'."""
    tmp = get_db_path(repo).parent / "weighted_tmp.db"
    shutil.copy(get_db_path(repo), tmp)
    store = GraphStore(tmp)
    clear(store)
    info = ""
    if mode != "static":
        edges = load_trace(Path(f"runtime/trace/{name}_trace.json"), pkg)
        record_observations(store, edges, run_id=f"{name}_trace")
        info = str(materialize(store, weighted=mode in ("weighted", "inverse"),
                               invert=(mode == "inverse"),
                               centered=(mode == "centered")))
    m = summarize(union_rr(store, seeds))
    store.close()
    tmp.unlink(missing_ok=True)
    return m, info


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    seeds = seeds_for(name, repo)

    print(f"=== {name} frequency weighting (n={len(seeds)} seeds) ===")
    results = {}
    for mode in ("static", "uniform", "weighted", "inverse", "centered"):
        m, info = variant(repo, name, pkg, seeds, mode)
        results[mode] = m
        print(f"{mode:>9}: {m}" + (f"   [{info}]" if info else ""))

    u, w = results["uniform"], results["weighted"]
    rel = 100 * (w["MRR"] - u["MRR"]) / u["MRR"] if u["MRR"] else 0.0
    d3 = 100 * (w["hit@3"] - u["hit@3"])
    print(f"\nweighted vs uniform: MRR {rel:+.1f}%   hit@3 {d3:+.1f}pp")
    print("pre-registered: >= +10% MRR and >= +3pp hit@3")
    print("NOTE: 'inverse' is an exploratory mechanism probe, not a pre-registered\n"
          "      variant. Any gain there needs fresh pre-registration on held-out\n"
          "      data before it counts as a result.")


if __name__ == "__main__":
    main()
