"""Static CALLS edges vs. edges that actually fired under the test suite.

Reports both directions: static edges that never fire (candidate phantom ->
precision), and fired edges the resolver never found (missed -> recall).
"""

import json
from pathlib import Path
from code_review_graph.graph import GraphStore
from code_review_graph.incremental import get_db_path

repo = Path("evaluate/test_repos/httpx").resolve()
pkg = str(repo / "httpx") + "/"
store = GraphStore(get_db_path(repo))

# in-package function nodes
node_files = {}
for n in store.get_nodes_by_kind(["Function", "Test"]):
    node_files[n.qualified_name] = n.file_path
in_pkg = lambda q: q in node_files and node_files[q].startswith(pkg)

# static internal CALLS edges (both ends resolve to in-pkg function nodes)
static = set()
unresolved_tgt = 0
for src, tgt in store._conn.execute(
        "SELECT source_qualified, target_qualified FROM edges WHERE kind='CALLS'"):
    if in_pkg(src) and in_pkg(tgt):
        static.add((src, tgt))
    elif in_pkg(src) and store.get_node(tgt) is None:
        unresolved_tgt += 1

trace = json.loads((Path("runtime/trace/httpx_trace.json")).read_text())
fired_all = {tuple(e) for e in trace["fired_edges"]}
fired = {(s, t) for s, t in fired_all if s.startswith(pkg) and t.startswith(pkg)}

print(f"static internal CALLS edges: {len(static)}")
print(f"  ...with unresolved (bare-name) targets from in-pkg sources: {unresolved_tgt}")
print(f"fired internal edges (pkg->pkg): {len(fired)}\n")

fired_nodes = {n for n in trace["fired_nodes"] if n.startswith(pkg)}
confirmed = static & fired
never_fired = static - fired
# unambiguous false negatives: fired, both endpoints are real graph nodes, not a static edge
missed_real = {(s, t) for s, t in fired if in_pkg(s) and in_pkg(t)} - static

# split never-fired: both endpoints executed (phantom-ish) vs an endpoint never ran (untested)
nf_both_ran = sum(1 for s, t in never_fired if s in fired_nodes and t in fired_nodes)
nf_untested = len(never_fired) - nf_both_ran

print(f"static edges CONFIRMED by firing:   {len(confirmed)}  "
      f"({100*len(confirmed)/max(len(static),1):.0f}% of static)")
print(f"static edges NEVER fired:            {len(never_fired)}  "
      f"({100*len(never_fired)/max(len(static),1):.0f}% of static)")
print(f"   - both endpoints ran (phantom?):  {nf_both_ran}")
print(f"   - an endpoint never ran (untested): {nf_untested}")
print(f"fired edges MISSED by resolver (both ends real nodes): {len(missed_real)}")
print(f"   static recall on internal call edges: "
      f"{100*len(confirmed)/max(len(confirmed)+len(missed_real),1):.0f}%")

# node level
static_nodes = {q for q, f in node_files.items() if f.startswith(pkg)}
print(f"\nin-pkg function nodes: {len(static_nodes)}  fired: {len(static_nodes & fired_nodes)}  "
      f"never-fired nodes: {len(static_nodes - fired_nodes)}")
store.close()
