# Runtime Execution Layer

Does runtime execution data make static impact analysis better?

This directory is my contribution to a fork of
[code-review-graph](https://github.com/tirth8205/code-review-graph). Everything
outside `runtime/` is Tirth Kanani's work: a Tree-sitter parser that builds a
call graph in SQLite and walks it to compute the "blast radius" of a change, so
an AI agent reads only the affected files instead of a whole repository. One
line in `code_review_graph/graph.py` is mine and is explained below.

The short version of what follows: I predicted the win would come from
**demoting edges that never execute**. That was wrong, measured at +0.0%. The
win came from **recovering calls that static analysis never found at all**, and
only for the *ranking* of the blast radius, not its membership.

---

## The question

The base tool's README documents its own weakness: call resolution and flow
detection are weak outside Python framework patterns. Static analysis of Python
is hard — dynamic dispatch, decorators, and indirection defeat it. That should
make the blast radius both noisy (edges that don't reflect real behaviour) and
incomplete (calls it never sees).

My hypothesis: if you record which call edges *actually fire* at runtime, you
can correct the graph and improve impact analysis. Specifically, that
statically-linked edges which never execute are false positives worth demoting.

## Method

### Instrument

`trace/call_tracer.py` installs a `sys.setprofile` hook and records
`(caller, callee)` pairs for every Python call into the target package. Both
ends are keyed as `co_filename::co_qualname`, which lines up with the graph's
`qualified_name` format on Python 3.11+.

Three instruments were tried and discarded first, which matters because each
would have produced a confident wrong answer:

1. **The benchmark's own one-hop predictor.** Its co-change mode passes
   repo-relative paths into a graph keyed by absolute paths, so lookups
   silently returned nothing: 901 of 944 seeds predicted an empty set. Any
   result built on it would have been an artifact of a path bug.
2. **Node-level line coverage.** On httpx, 99.98% of impact-radius source nodes
   execute under the test suite. A library's suite is engineered toward total
   coverage, so "did this function ever run" discriminates almost nothing.
3. **Node-level coverage including test files.** This appeared to show 61% of
   predicted files were dead — but only because coverage was scoped to the
   package, so test files had no data and were miscounted. Controlling for that
   collapsed the signal to 0.2%.

The lesson from all three: the over-approximation lives in the **edges**, not
the nodes. A phantom edge `A -> B` has both endpoints covered — `B` runs, just
never because `A` called it. Only call-level tracing can see that.

### Evaluation

The upstream impact benchmark is 13 hand-picked commits, 2–3 per repo, scored
at file-set level. On the Python targets, roughly 70% of the improvable surface
sits in a single flask commit, so any measured delta would be one commit's
noise.

`eval/mine_commits.py` expands it using the same methodology: seed the
predictor with one changed file, grade against the other files the author
touched in that commit. Two changes:

- **Per-file seeding.** Upstream seeds `sorted(changed)[0]`, the
  alphabetically-first changed file, which lands on a package source file only
  8% of the time in fastapi. Every package source file in a commit is seeded in
  turn instead.
- **More commits**, selected by a filter frozen before any result was computed:
  non-merge, at least 2 package source files changed, at most 15 files total,
  within the most recent 2000 commits before the pinned snapshot.

| repo    | commits | graded predictions |
|---------|--------:|-------------------:|
| fastapi |      29 |                102 |
| flask   |     108 |                340 |
| httpx   |     274 |                944 |
| total   |     411 |            **1386** |

Thresholds for "this counts as a real effect" were written down before each run
rather than after.

---

## Findings

### 1. Static call resolution is badly incomplete

Comparing the static `CALLS` edges to what actually fired
(`eval/edge_analysis.py`):

| metric | httpx | flask |
|---|---:|---:|
| static internal call edges | 225 | 181 |
| confirmed by firing | 157 (70%) | 86 (48%) |
| never fired | 68 (30%) | 95 (52%) |
| fired edges the resolver missed | **496** | **171** |
| **static recall on internal call edges** | **24%** | **33%** |

The resolver finds roughly one in four real internal calls. For context, only
499 of httpx's 4,199 `CALLS` edges resolve to a real node at all; the rest are
unbound bare names like `print` or `startswith`. The `confidence` field is
uniformly `1.0`/`EXTRACTED`, so it carries no gradient — execution data is not
re-deriving a signal the graph already had.

### 2. Recovering those edges substantially improves blast-radius ranking

Feeding the recovered edges back in and re-ranking (`eval/ranking_union.py`),
scored against co-change over every mined seed:

| repo | MRR (static → augmented) | hit@1 | hit@3 |
|---|---|---|---|
| httpx (n=944) | 0.170 → 0.244 (**+43.6%**) | 7.5% → 14.7% | 22.8% → 30.9% |
| flask (n=340) | 0.220 → 0.604 (**+174.8%**) | 12.4% → 53.2% | 25.0% → 64.1% |

Both clear the pre-registered bar (≥ +15% MRR, ≥ +5pp hit@3). The effect size
tracks how incomplete the static graph was to begin with, which is a mechanism
rather than a coincidence: flask's resolver had more to miss.

### 3. My actual hypothesis was wrong

An ablation separates the two interventions:

| variant | httpx MRR | flask MRR |
|---|---|---|
| baseline (static) | 0.170 | 0.220 |
| prune never-fired edges only | 0.170 (**+0.0%**) | 0.226 (**+2.9%**) |
| add recovered edges only | 0.244 (+43.6%) | 0.601 (+173.5%) |
| both | 0.244 (+43.6%) | 0.604 (+174.8%) |

Pruning contributes essentially nothing. "Demote edges that never fire" — the
thing I set out to test — is refuted. The static graph's problem was not
phantom edges to remove, it was true edges it never saw.

The improvement is also specific to **ranking**. On set membership
(`eval/augmented_eval.py`), the same edges move precision +0.4pp and recall
+1.7pp on httpx: near-flat. Call structure and co-change are only weakly
coupled — files that call each other are not necessarily files an author edits
together. So runtime data improves the *order* of the blast radius, not *which
files are in it*.

This is worth stating plainly because it is the part I got wrong. An
intermediate metric I could move a lot (edge recall, 24% → near-complete) did
not propagate to the end metric it was supposed to improve.

### 4. Execution *frequency* carries no usable signal either

The tracer counts invocations, not just presence, so the other form of the
original thesis is testable: weight each recovered edge by how often it fired.
Counts are heavily skewed (httpx median 2, max 48,675), so the weight is a
log-scaled hit count, fixed in advance rather than swept.

Baseline here is the *augmented* graph, since the question is whether frequency
adds anything beyond presence. Pre-registered bar: >= +10% MRR, >= +3pp hit@3.

| variant | httpx MRR | flask MRR |
|---|---:|---:|
| uniform (presence only) | **0.244** | **0.601** |
| weighted by frequency | 0.234 (-4.3%) | 0.386 (-35.8%) |
| mean-preserving weighting | 0.239 (-1.9%) | 0.382 (-36.4%) |
| inverse weighting (diagnostic) | 0.240 | 0.448 |

Frequency weighting does not merely fail the bar, it actively hurts, on both
repos.

The first weighting scheme was confounded and the diagnostic row is what caught
it. My initial guess was that rare edges are the informative ones -- a
specialised function tied to one feature should co-change more than a hot
utility that is stable infrastructure. If that were true, *inverse* weighting
should beat uniform. It does not: uniform beats both directions. The real
problem was that mapping onto `[MIN_WEIGHT, 1.0]` put almost every runtime edge
*below* the static edges' 1.0, so the variant conflated frequency-awareness
with a blanket demotion of the very edges that produce the gain. The
mean-preserving variant removes that confound and still loses.

So both forms of "weight edges by how they behave at runtime" are refuted:
binary pruning of never-fired edges (+0.0%) and graded weighting by frequency
(-2% to -36%). The only intervention that helps is recovering edges the
resolver never found. **The static graph's deficiency is entirely what it is
missing, not what it wrongly includes or over-weights.**

### 5. The result is not an artefact of exhaustive test coverage

Every finding above draws its runtime signal from each project's own test
suite, which is written for coverage rather than representativeness. That was
the largest caveat on this work, so it is worth testing directly rather than
just declaring.

`workload/httpx_workload.py` drives httpx the way an application does — a
handful of endpoints hit repeatedly, through redirects, auth, streaming, error
handling and client reuse — against an in-process WSGI app, so it is
deterministic and needs no network while still exercising the real client and
transport stack.

| httpx (n=944) | edges recovered | MRR | vs static |
|---|---:|---:|---:|
| static | — | 0.170 | — |
| test suite | 498 | 0.244 | +43.6% |
| application workload | 162 | 0.213 | **+25.0%** |

The workload clears the pre-registered +15% bar, so the effect is not an
artefact of exhaustive testing. It also recovers about a third of the edges the
suite does while delivering a little over half the benefit, which suggests a
short representative run captures most of the value — a more practical
proposition than asking users to trace an entire test suite.

This narrows the caveat rather than removing it: the workload is
application-shaped but still synthetic, and it is one library. Tracing genuine
production traffic remains untested.

---

## From experiment to feature

The result is not a notebook. `augment.py` and `cli.py` make it something you
run:

```bash
python runtime/trace/trace_repo.py httpx   # with the target's own venv
python -m runtime record httpx             # persist observed edges
python -m runtime augment httpx            # merge them into the graph
python -m runtime status httpx
```

Observed edges live in their own `runtime_edges` table with provenance, and are
materialized into `edges` as `RUNTIME`-tier. Materialization is idempotent and
validates both endpoints against the current graph, so an observation recorded
against older source is dropped and reported rather than silently kept. On
httpx: 755 observed, 498 materialized, 157 the parser already had, 100 stale
(lambdas and nested functions the tracer sees but the parser does not node-ify).

**The one upstream change.** `remove_file_data` deleted every edge whose
`file_path` matched a re-parsed file. Runtime edges are *observed*, not derived,
so nothing regenerates them — they were silently wiped on the next incremental
update. It now deletes only edges derived from parsing that file. Verified: a
re-parse clears that file's 374 static edges and leaves its 150 runtime edges
intact.

`eval/verify_tool.py` confirms the shipped path reproduces the measured result
exactly (MRR 0.170 → 0.244, +43.6%), rather than the improvement existing only
in the experiment's throwaway database.

---

## Reproducing

```bash
python -m venv .venv && ./.venv/bin/pip install -e ".[enrichment,eval]"
# clone targets into evaluate/test_repos/<name> at the pinned SHA in eval/mine_commits.py
./.venv/bin/python runtime/eval/mine_commits.py          # build the eval set
# create a venv per target with its test deps, then:
./.venv/bin/python runtime/trace/trace_repo.py httpx     # target's venv
./.venv/bin/python runtime/eval/edge_analysis.py httpx   # finding 1
./.venv/bin/python runtime/eval/ranking_union.py httpx   # finding 2 and 3
./.venv/bin/python runtime/eval/augmented_eval.py httpx  # set-membership result
```

Note: flask's test suite needs `pytest==8.1.1`; newer pytest removed the
private `_pytest.monkeypatch.notset` that its `test_cli.py` imports.

---

## Limitations

These are real and I would rather state them than have them found.

- **Runtime signal comes from test suites and one synthetic workload, not
  production traffic.** Finding 5 shows the ranking result survives on an
  application-shaped workload (+25.0%), which narrows this considerably, but
  that workload is still synthetic and covers one library. Separately, the
  never-fired finding remains suite-dependent: an edge on an unexercised path
  also never fires, so 30%/52% is an upper bound on phantoms.
- **Ground truth is co-change**, which is a proxy for "impact" and a noisy one.
  Commits bundle unrelated edits. This is inherited from the benchmark, and
  Finding 3 suggests it may not measure what blast radius is supposed to mean.
  I froze the selection filter before looking at results specifically so this
  could not be tuned after the fact.
- **The ranking metric blends coverage and ordering.** A seed whose radius
  contains no true file scores RR=0 rather than being dropped, which keeps all
  seeds like-for-like across variants but means these numbers are not
  comparable to a pure ordering-only MRR.
- **Two repos, not three.** Ranking is replicated on httpx and flask; fastapi is
  traced but not yet evaluated for ranking.
- **Correlation is coarse.** Frames map to nodes by function line-range
  containment. Decorators that shift line numbers, async frames, and nested
  functions are handled crudely — the 100 stale observations on httpx are
  mostly this.
- **Python only.** Go and JavaScript are where the base tool's flow detection is
  weakest and so where this would be most valuable, but span-to-source
  correlation through transpiled and bundled output is a different problem.

## What I would do next

1. Evaluate ranking on fastapi, for a third replication.
2. Trace a non-test workload — an application exercising one of these libraries
   under realistic traffic — to test whether the finding survives outside a
   test suite.
3. Improve frame-to-node correlation for decorators and async frames, and
   measure how many of the stale observations that recovers.
