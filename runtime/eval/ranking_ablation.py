"""Harden the ranking result: identical seed set, and ablate the two changes.

ranking_eval scored each graph on the seeds it happened to cover (436 vs 445),
so the comparison wasn't like-for-like. Here every variant is scored on the
intersection of seeds covered by ALL variants, and the augmentation is split
into its two independent parts.
"""

import shutil
import statistics
import sys
from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path
from runtime.eval.mine_commits import TARGETS
from runtime.eval.augmented_eval import (
    seeds_for, runtime_edge_sets, add_edges, remove_edges,
)
from runtime.eval.ranking_eval import ranked_files


def per_seed_rr(store, seeds):
    """seed -> reciprocal rank of first true co-changed file (only if covered)."""
    out = {}
    for seed, co in seeds:
        hits = [i for i, f in enumerate(ranked_files(store, seed), 1) if f in co]
        if hits:
            out[seed] = 1.0 / hits[0]
    return out


def summarize(rr, keys):
    vals = [rr[k] for k in keys]
    return {
        "MRR": round(statistics.mean(vals), 4),
        "hit@1": round(sum(v == 1.0 for v in vals) / len(vals), 3),
        "hit@3": round(sum(v >= 1 / 3 for v in vals) / len(vals), 3),
    }


def variant(repo, name, missed, phantom, node_files, add, prune):
    path = get_db_path(repo).parent / f"abl_{name}.db"
    shutil.copy(get_db_path(repo), path)
    s = GraphStore(path)
    if add:
        add_edges(s, missed, node_files)
    if prune:
        remove_edges(s, phantom)
    return s, path


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    seeds = seeds_for(name, repo)

    base = GraphStore(get_db_path(repo))
    missed, phantom, node_files = runtime_edge_sets(base, name, repo, pkg)
    rr = {"baseline": per_seed_rr(base, seeds)}
    base.close()

    for label, add, prune in [("+edges only", True, False),
                              ("-phantom only", False, True),
                              ("+edges -phantom", True, True)]:
        s, path = variant(repo, name, missed, phantom, node_files, add, prune)
        rr[label] = per_seed_rr(s, seeds)
        s.close()
        path.unlink(missing_ok=True)

    common = set.intersection(*(set(d) for d in rr.values()))
    print(f"=== {name} ranking ablation ===")
    print(f"seeds covered by every variant: {len(common)} "
          f"(individually: {[len(d) for d in rr.values()]})")
    b = summarize(rr["baseline"], common)
    print(f"{'baseline':>18}: {b}")
    for label in ("+edges only", "-phantom only", "+edges -phantom"):
        m = summarize(rr[label], common)
        rel = 100 * (m["MRR"] - b["MRR"]) / b["MRR"]
        print(f"{label:>18}: {m}   MRR {rel:+.1f}%")


if __name__ == "__main__":
    main()
