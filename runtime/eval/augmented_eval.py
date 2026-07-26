"""Does a runtime-augmented call graph change impact-analysis accuracy?

Baseline: get_impact_radius (depth 2) over the static graph, graded against
co-change. Then add the runtime-observed call edges the static resolver
missed, and (optionally) drop the static edges that never fired, and re-grade.
Same predictor, same seeds, same scorer -- only the edge set changes.
"""

import json
import shutil
import statistics
import sys
import time
from pathlib import Path

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path
from runtime.eval.mine_commits import TARGETS

DEPTH = 2


def seeds_for(name, repo):
    m = json.loads(Path(f"runtime/eval/manifests/{name}.json").read_text())
    out = []
    for c in m["commits"]:
        changed = {str(repo / p) for p in c["changed"]}
        for s in c["seeds"]:
            seed = str(repo / s)
            co = changed - {seed}
            if co:
                out.append((seed, co))
    return out


def evaluate(store, seeds):
    prec, rec = [], []
    empty = 0
    for seed, co in seeds:
        r = store.get_impact_radius([seed], max_depth=DEPTH)
        pred = set(r["impacted_files"])
        pred.discard(seed)
        if not pred:
            empty += 1
            continue
        tp = len(pred & co)
        prec.append(tp / len(pred))
        rec.append(tp / len(co))
    return {
        "n": len(prec), "empty": empty,
        "precision": round(statistics.mean(prec), 3) if prec else 0.0,
        "recall": round(statistics.mean(rec), 3) if rec else 0.0,
    }


def runtime_edge_sets(store, name, repo, pkg):
    """(missed_real, phantom) using the same logic as edge_analysis."""
    node_files = {n.qualified_name: n.file_path
                  for n in store.get_nodes_by_kind(["Function", "Test"])}
    in_pkg = lambda q: q in node_files and node_files[q].startswith(pkg)
    static = {(s, t) for s, t in store._conn.execute(
        "SELECT source_qualified, target_qualified FROM edges WHERE kind='CALLS'")
        if in_pkg(s) and in_pkg(t)}
    trace = json.loads(Path(f"runtime/trace/{name}_trace.json").read_text())
    fired = {(s, t) for s, t in (tuple(e) for e in trace["fired_edges"])
             if s.startswith(pkg) and t.startswith(pkg)}
    fired_nodes = {n for n in trace["fired_nodes"] if n.startswith(pkg)}
    missed = {(s, t) for s, t in fired if in_pkg(s) and in_pkg(t)} - static
    phantom = {(s, t) for s, t in (static - fired)
               if s in fired_nodes and t in fired_nodes}
    return missed, phantom, node_files


def add_edges(store, edges, node_files):
    now = time.time()
    store._conn.executemany(
        "INSERT INTO edges (kind, source_qualified, target_qualified, file_path, "
        "line, extra, confidence, confidence_tier, updated_at) "
        "VALUES ('CALLS', ?, ?, ?, 0, '{}', 1.0, 'RUNTIME', ?)",
        [(s, t, node_files.get(s, ""), now) for s, t in edges])
    store._conn.commit()


def remove_edges(store, edges):
    store._conn.executemany(
        "DELETE FROM edges WHERE kind='CALLS' AND source_qualified=? AND target_qualified=?",
        list(edges))
    store._conn.commit()


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    seeds = seeds_for(name, repo)

    base_store = GraphStore(get_db_path(repo))
    missed, phantom, node_files = runtime_edge_sets(base_store, name, repo, pkg)
    print(f"=== {name} === seeds={len(seeds)}  +missed={len(missed)}  -phantom={len(phantom)}")
    print("baseline (static):        ", evaluate(base_store, seeds))
    base_store.close()

    # work on a copy so the real graph is untouched
    aug_path = get_db_path(repo).parent / "augmented.db"
    shutil.copy(get_db_path(repo), aug_path)
    aug = GraphStore(aug_path)
    add_edges(aug, missed, node_files)
    print("augmented (+runtime edges):", evaluate(aug, seeds))
    remove_edges(aug, phantom)
    print("augmented + phantom-pruned:", evaluate(aug, seeds))
    aug.close()
    aug_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
