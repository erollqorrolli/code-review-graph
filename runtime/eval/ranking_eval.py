"""Does runtime correction improve the RANKING of the blast radius?

The set-membership test (augmented_eval) was flat. This tests the original
thesis form: reorder the blast radius so files reached via edges that actually
fire rank above files reached only via phantom edges, and measure whether the
true co-changed files move toward the top.

Pre-registered primary metric: MRR over seeds where >=1 true file is in the
radius. Success = >=20% relative improvement of augmented+pruned over static.
"""

import json
import shutil
import statistics
import sys
from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path
from runtime.eval.mine_commits import TARGETS
from runtime.eval.augmented_eval import (
    seeds_for, runtime_edge_sets, add_edges, remove_edges, DEPTH,
)


def ranked_files(store, seed):
    """Blast-radius files ranked by best per-file impact score (desc)."""
    r = store.get_impact_radius([seed], max_depth=DEPTH)
    best = {}
    for qn, score in r["impact_scores"].items():
        f = qn.rsplit("::", 1)[0]
        if f == seed:
            continue
        if f not in best or score > best[f]:
            best[f] = score
    return [f for f, _ in sorted(best.items(), key=lambda kv: -kv[1])]


def rank_metrics(store, seeds):
    rrs, hit1, hit3, covered = [], 0, 0, 0
    for seed, co in seeds:
        ranked = ranked_files(store, seed)
        hits = [i for i, f in enumerate(ranked, 1) if f in co]
        if not hits:
            continue
        covered += 1
        r1 = hits[0]
        rrs.append(1.0 / r1)
        hit1 += (r1 == 1)
        hit3 += (r1 <= 3)
    n = len(rrs)
    return {
        "covered_seeds": covered,
        "MRR": round(statistics.mean(rrs), 4) if rrs else 0.0,
        "hit@1": round(hit1 / n, 3) if n else 0.0,
        "hit@3": round(hit3 / n, 3) if n else 0.0,
    }


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    seeds = seeds_for(name, repo)

    base = GraphStore(get_db_path(repo))
    missed, phantom, node_files = runtime_edge_sets(base, name, repo, pkg)
    b = rank_metrics(base, seeds)
    base.close()

    aug_path = get_db_path(repo).parent / "ranking.db"
    shutil.copy(get_db_path(repo), aug_path)
    aug = GraphStore(aug_path)
    add_edges(aug, missed, node_files)
    remove_edges(aug, phantom)
    a = rank_metrics(aug, seeds)
    aug.close()
    aug_path.unlink(missing_ok=True)

    print(f"=== {name} ranking (+missed={len(missed)} -phantom={len(phantom)}) ===")
    print("baseline (static):         ", b)
    print("augmented + phantom-pruned:", a)
    if b["MRR"]:
        rel = 100 * (a["MRR"] - b["MRR"]) / b["MRR"]
        print(f"\nMRR relative change: {rel:+.1f}%   (pre-registered success: >= +20%)")


if __name__ == "__main__":
    main()
