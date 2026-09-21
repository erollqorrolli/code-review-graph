"""Durable storage for observed call edges, and merging them into the graph.

Runtime edges are a different kind of fact from parsed ones: they are
*observed*, not derived, so nothing regenerates them when a file is re-parsed.
They live in their own table and are materialized into ``edges`` on demand.

An observation can also go stale -- a function that was called under an old
commit may not exist any more -- so materializing validates both endpoints
against the current graph and reports what it dropped.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

RUNTIME_TIER = "RUNTIME"

# Confidence floor for a runtime edge that fired exactly once. Weighting maps
# log-scaled hit counts onto [MIN_WEIGHT, 1.0]. Fixed in advance rather than
# swept, so the weighted variant is one pre-registered configuration.
MIN_WEIGHT = 0.25

SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_edges (
    source_qualified TEXT NOT NULL,
    target_qualified TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    observed_at      REAL NOT NULL,
    hit_count        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source_qualified, target_qualified, run_id)
);
CREATE INDEX IF NOT EXISTS idx_runtime_edges_src ON runtime_edges(source_qualified);
"""


@dataclass
class AugmentResult:
    observed: int      # distinct observed edges on record
    materialized: int  # inserted into the graph
    stale: int         # skipped: an endpoint no longer exists
    already_static: int  # the parser had already found these

    def __str__(self) -> str:
        return (f"observed={self.observed} materialized={self.materialized} "
                f"stale={self.stale} already_static={self.already_static}")


def ensure_schema(store) -> None:
    store._conn.executescript(SCHEMA)
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(runtime_edges)")}
    if "hit_count" not in cols:  # table predates counting
        store._conn.execute(
            "ALTER TABLE runtime_edges ADD COLUMN hit_count INTEGER NOT NULL DEFAULT 1")
    store._conn.commit()


def record_observations(store, edges, run_id: str) -> int:
    """Persist observed edges. Idempotent per run_id.

    *edges* is either (caller, callee) pairs or (caller, callee, hit_count)
    triples; a pair is recorded with a count of 1.
    """
    ensure_schema(store)
    now = time.time()
    rows = [(e[0], e[1], run_id, now, e[2] if len(e) > 2 else 1) for e in edges]
    store._conn.executemany(
        "INSERT OR REPLACE INTO runtime_edges "
        "(source_qualified, target_qualified, run_id, observed_at, hit_count) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    store._conn.commit()
    return len(rows)


def load_trace(path: Path, pkg_prefix: str):
    """Read a tracer dump, keeping only in-package caller->callee edges.

    Returns (caller, callee, hit_count) triples when the dump has counts,
    otherwise (caller, callee) pairs from an older dump.
    """
    data = json.loads(Path(path).read_text())
    if "fired_edge_counts" in data:
        return [(s, t, n) for s, t, n in data["fired_edge_counts"]
                if s.startswith(pkg_prefix) and t.startswith(pkg_prefix)]
    return [(s, t) for s, t in (tuple(e) for e in data["fired_edges"])
            if s.startswith(pkg_prefix) and t.startswith(pkg_prefix)]


def materialize(store, weighted: bool = False, invert: bool = False,
                centered: bool = False) -> AugmentResult:
    """Merge observed edges into ``edges`` as RUNTIME-tier. Idempotent.

    Skips observations whose endpoints are no longer in the graph (stale) and
    those the parser already resolved on its own (already_static).

    With ``weighted``, an edge's confidence is its log-scaled hit count mapped
    onto [MIN_WEIGHT, 1.0], so a call that fired once propagates less impact
    than one that fired constantly. Unweighted, every observed edge is 1.0 --
    presence only. Whether frequency carries signal beyond presence is the
    question this flag exists to answer.
    """
    ensure_schema(store)
    store._conn.execute(
        "DELETE FROM edges WHERE confidence_tier = ?", (RUNTIME_TIER,))

    observed = list(store._conn.execute(
        "SELECT source_qualified, target_qualified, MAX(hit_count) FROM runtime_edges "
        "GROUP BY source_qualified, target_qualified"))
    static = {(s, t) for s, t in store._conn.execute(
        "SELECT source_qualified, target_qualified FROM edges WHERE kind = 'CALLS'")}
    nodes = {q for (q,) in store._conn.execute("SELECT qualified_name FROM nodes")}
    node_file = dict(store._conn.execute("SELECT qualified_name, file_path FROM nodes"))

    peak = math.log1p(max((c for _, _, c in observed), default=1))
    rows, stale, already = [], 0, 0
    now = time.time()
    for src, tgt, count in observed:
        if src not in nodes or tgt not in nodes:
            stale += 1
            continue
        if (src, tgt) in static:
            already += 1
            continue
        frac = math.log1p(count) / peak if peak > 0 else 1.0
        if invert:  # diagnostic only: is rarity the useful signal?
            frac = 1.0 - frac
        if centered:
            conf = frac          # normalised to mean 1.0 below
        elif weighted:
            conf = MIN_WEIGHT + (1.0 - MIN_WEIGHT) * frac
        else:
            conf = 1.0
        rows.append((src, tgt, node_file.get(src, ""), conf, now))

    if centered and rows:
        # Mean-preserving: frequency changes the *relative* strength of runtime
        # edges without demoting them as a class below static edges (1.0),
        # which is what the [MIN_WEIGHT, 1.0] mapping silently did.
        mean = sum(r[3] for r in rows) / len(rows)
        if mean > 0:
            rows = [(s, tg, f, c / mean, n) for s, tg, f, c, n in rows]

    store._conn.executemany(
        "INSERT INTO edges (kind, source_qualified, target_qualified, file_path, "
        "line, extra, confidence, confidence_tier, updated_at) "
        f"VALUES ('CALLS', ?, ?, ?, 0, '{{}}', ?, '{RUNTIME_TIER}', ?)",
        rows,
    )
    store._conn.commit()
    store._invalidate_cache()
    return AugmentResult(len(observed), len(rows), stale, already)


def clear(store) -> int:
    """Drop materialized runtime edges (observations are kept)."""
    cur = store._conn.execute(
        "DELETE FROM edges WHERE confidence_tier = ?", (RUNTIME_TIER,))
    store._conn.commit()
    store._invalidate_cache()
    return cur.rowcount
