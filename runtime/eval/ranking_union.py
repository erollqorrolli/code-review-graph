"""Union-scored ranking: the defensible number.

ranking_ablation intersected the seeds every variant covered, which collapsed
to n=22 -- underpowered. Here every variant is scored on the SAME full seed
set: a seed whose radius contains no true co-changed file scores RR=0 rather
than being dropped.

That keeps all seeds and stays like-for-like across variants, at the cost of
folding a coverage failure into the ranking metric. Union-MRR is therefore a
BLENDED coverage+ordering score and is not comparable to the ordering-only
MRR reported by ranking_ablation.

Pre-registered: >= +15% relative union-MRR (and >= +5pp hit@3) counts as real.
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


def union_rr(store, seeds):
    """Reciprocal rank per seed, 0.0 when no true file is in the radius."""
    out = []
    for seed, co in seeds:
        hits = [i for i, f in enumerate(ranked_files(store, seed), 1) if f in co]
        out.append(1.0 / hits[0] if hits else 0.0)
    return out


def summarize(rrs):
    return {
        "n": len(rrs),
        "MRR": round(statistics.mean(rrs), 4),
        "hit@1": round(sum(v == 1.0 for v in rrs) / len(rrs), 3),
        "hit@3": round(sum(v >= 1 / 3 for v in rrs) / len(rrs), 3),
        "zero": round(sum(v == 0.0 for v in rrs) / len(rrs), 3),
    }


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    seeds = seeds_for(name, repo)

    base = GraphStore(get_db_path(repo))
    missed, phantom, node_files = runtime_edge_sets(base, name, repo, pkg)
    results = {"baseline": summarize(union_rr(base, seeds))}
    base.close()

    for label, add, prune in [("+edges only", True, False),
                              ("-phantom only", False, True),
                              ("+edges -phantom", True, True)]:
        path = get_db_path(repo).parent / "union_tmp.db"
        shutil.copy(get_db_path(repo), path)
        s = GraphStore(path)
        if add:
            add_edges(s, missed, node_files)
        if prune:
            remove_edges(s, phantom)
        results[label] = summarize(union_rr(s, seeds))
        s.close()
        path.unlink(missing_ok=True)

    b = results["baseline"]
    print(f"=== {name} union-scored ranking (all {len(seeds)} seeds, RR=0 if uncovered) ===")
    print(f"{'baseline':>18}: {b}")
    for label in ("+edges only", "-phantom only", "+edges -phantom"):
        m = results[label]
        rel = 100 * (m["MRR"] - b["MRR"]) / b["MRR"]
        d3 = 100 * (m["hit@3"] - b["hit@3"])
        print(f"{label:>18}: {m}   MRR {rel:+.1f}%  hit@3 {d3:+.1f}pp")
    print("\npre-registered: >= +15% MRR and >= +5pp hit@3")
    print("NOTE: blended coverage+ordering score; not comparable to ordering-only MRR.")


if __name__ == "__main__":
    main()
