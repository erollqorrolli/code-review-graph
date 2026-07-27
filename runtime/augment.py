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
import time
from dataclasses import dataclass
from pathlib import Path

RUNTIME_TIER = "RUNTIME"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_edges (
    source_qualified TEXT NOT NULL,
    target_qualified TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    observed_at      REAL NOT NULL,
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
    store._conn.commit()


def record_observations(store, edges, run_id: str) -> int:
    """Persist observed (caller, callee) pairs. Idempotent per run_id."""
    ensure_schema(store)
    now = time.time()
    store._conn.executemany(
        "INSERT OR REPLACE INTO runtime_edges "
        "(source_qualified, target_qualified, run_id, observed_at) VALUES (?, ?, ?, ?)",
        [(s, t, run_id, now) for s, t in edges],
    )
    store._conn.commit()
    return len(edges)


def load_trace(path: Path, pkg_prefix: str) -> list[tuple[str, str]]:
    """Read a tracer dump, keeping only in-package caller->callee pairs."""
    data = json.loads(Path(path).read_text())
    return [(s, t) for s, t in (tuple(e) for e in data["fired_edges"])
            if s.startswith(pkg_prefix) and t.startswith(pkg_prefix)]


def materialize(store) -> AugmentResult:
    """Merge observed edges into ``edges`` as RUNTIME-tier. Idempotent.

    Skips observations whose endpoints are no longer in the graph (stale) and
    those the parser already resolved on its own (already_static).
    """
    ensure_schema(store)
    store._conn.execute(
        "DELETE FROM edges WHERE confidence_tier = ?", (RUNTIME_TIER,))

    observed = list(store._conn.execute(
        "SELECT DISTINCT source_qualified, target_qualified FROM runtime_edges"))
    static = {(s, t) for s, t in store._conn.execute(
        "SELECT source_qualified, target_qualified FROM edges WHERE kind = 'CALLS'")}
    nodes = {q for (q,) in store._conn.execute("SELECT qualified_name FROM nodes")}
    node_file = dict(store._conn.execute("SELECT qualified_name, file_path FROM nodes"))

    rows, stale, already = [], 0, 0
    now = time.time()
    for src, tgt in observed:
        if src not in nodes or tgt not in nodes:
            stale += 1
            continue
        if (src, tgt) in static:
            already += 1
            continue
        rows.append((src, tgt, node_file.get(src, ""), now))

    store._conn.executemany(
        "INSERT INTO edges (kind, source_qualified, target_qualified, file_path, "
        "line, extra, confidence, confidence_tier, updated_at) "
        f"VALUES ('CALLS', ?, ?, ?, 0, '{{}}', 1.0, '{RUNTIME_TIER}', ?)",
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
