"""Static CALLS edges vs. edges that actually fired under the test suite.

Usage: python runtime/eval/edge_analysis.py <repo-name>
Reports both directions: static edges that never fire (candidate phantom ->
precision), and fired edges the resolver never found (missed -> recall).
"""

import json
import sys
from pathlib import Path
from code_review_graph.graph import GraphStore
from code_review_graph.incremental import full_build, get_db_path
from code_review_graph.postprocessing import run_post_processing
from runtime.eval.mine_commits import TARGETS


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "httpx"
    repo = (Path("evaluate/test_repos") / name).resolve()
    pkg = str((repo / TARGETS[name]["pkg_dirs"][0]).resolve()) + "/"
    store = GraphStore(get_db_path(repo))

    if not store.get_nodes_by_kind(["Function"]):
        full_build(repo, store)
        run_post_processing(store)

    node_files = {n.qualified_name: n.file_path
                  for n in store.get_nodes_by_kind(["Function", "Test"])}
    in_pkg = lambda q: q in node_files and node_files[q].startswith(pkg)

    static = set()
    unresolved_tgt = 0
    for src, tgt in store._conn.execute(
            "SELECT source_qualified, target_qualified FROM edges WHERE kind='CALLS'"):
        if in_pkg(src) and in_pkg(tgt):
            static.add((src, tgt))
        elif in_pkg(src) and store.get_node(tgt) is None:
            unresolved_tgt += 1

    trace = json.loads((Path("runtime/trace") / f"{name}_trace.json").read_text())
    fired = {(s, t) for s, t in (tuple(e) for e in trace["fired_edges"])
             if s.startswith(pkg) and t.startswith(pkg)}
    fired_nodes = {n for n in trace["fired_nodes"] if n.startswith(pkg)}

    confirmed = static & fired
    never_fired = static - fired
    missed_real = {(s, t) for s, t in fired if in_pkg(s) and in_pkg(t)} - static
    nf_both_ran = sum(1 for s, t in never_fired if s in fired_nodes and t in fired_nodes)
    recall = 100 * len(confirmed) / max(len(confirmed) + len(missed_real), 1)

    print(f"=== {name} ===")
    print(f"static internal CALLS edges: {len(static)}  "
          f"(+{unresolved_tgt} in-pkg sources -> unresolved bare-name targets)")
    print(f"fired internal edges (pkg->pkg): {len(fired)}")
    print(f"confirmed by firing:   {len(confirmed)}  "
          f"({100*len(confirmed)/max(len(static),1):.0f}% of static)")
    print(f"never fired:           {len(never_fired)}  "
          f"({100*len(never_fired)/max(len(static),1):.0f}% of static; "
          f"{nf_both_ran} phantom-ish, {len(never_fired)-nf_both_ran} untested)")
    print(f"MISSED by resolver:    {len(missed_real)}")
    print(f">>> static recall on internal call edges: {recall:.0f}%")
    store.close()


if __name__ == "__main__":
    main()
