"""Select impact-analysis eval commits from a target's history.

This is selection only: it walks non-merge commits back from a pinned
snapshot, keeps the ones that touch enough package source, and writes a
manifest of (commit -> seeds, changed files). Nothing here scores anything,
so the filter can be frozen before any precision number exists.

Per-file seeding: every package source file in a commit becomes a seed,
graded later against the other files the author touched. Upstream seeds only
the alphabetically-first changed file, which lands on a source file ~8% of
the time and throws the rest of the signal away.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

WINDOW = 2000
MAX_FILES = 15
MIN_SEEDS = 2

TARGETS = {
    "fastapi": {
        "pin": "22381558446c5d1ac376680a6581dd63b3a04119",
        "pkg_dirs": ("fastapi/",),
    },
    "flask": {
        "pin": "a29f88ce6f2f9843bd6fcbbfce1390a2071965d6",
        "pkg_dirs": ("src/flask/",),
    },
    "httpx": {
        "pin": "b55d4635701d9dc22928ee647880c76b078ba3f2",
        "pkg_dirs": ("httpx/",),
    },
}


def is_test(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        base.startswith("test_")
        or base.endswith("_test.py")
        or base == "conftest.py"
        or "/tests/" in path
        or path.startswith("tests/")
    )


def is_package_source(path: str, pkg_dirs: tuple[str, ...]) -> bool:
    return (
        path.endswith(".py")
        and not is_test(path)
        and any(path.startswith(d) for d in pkg_dirs)
    )


@dataclass
class Commit:
    sha: str
    seeds: list[str]      # package source files, each seeded in turn
    changed: list[str]    # every path the author touched (co-change ground truth)


def walk_history(repo: Path, pin: str) -> list[tuple[str, list[str]]]:
    """Return (sha, changed_files) for non-merge commits back from *pin*.

    One `git log --name-only` pass instead of per-commit diffs: over a blobless
    clone the latter lazy-fetches a tree each and does not finish.
    """
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--no-merges", "-n", str(WINDOW),
         "--name-only", "--pretty=format:@@%H"],
        capture_output=True, text=True, check=True,
    ).stdout

    commits: list[tuple[str, list[str]]] = []
    sha, files = None, []
    for line in out.splitlines():
        if line.startswith("@@"):
            if sha is not None:
                commits.append((sha, files))
            sha, files = line[2:], []
        elif line.strip():
            files.append(line.strip())
    if sha is not None:
        commits.append((sha, files))
    return commits


def select(repo: Path, pin: str, pkg_dirs: tuple[str, ...]) -> list[Commit]:
    kept = []
    for sha, changed in walk_history(repo, pin):
        if not changed or len(changed) > MAX_FILES:
            continue
        seeds = [p for p in changed if is_package_source(p, pkg_dirs)]
        if len(seeds) >= MIN_SEEDS:
            kept.append(Commit(sha=sha, seeds=sorted(seeds), changed=sorted(changed)))
    return kept


def mine(name: str, repos_dir: Path, out_dir: Path) -> Path:
    cfg = TARGETS[name]
    repo = repos_dir / name
    commits = select(repo, cfg["pin"], tuple(cfg["pkg_dirs"]))
    manifest = {
        "repo": name,
        "pin": cfg["pin"],
        "filter": {"window": WINDOW, "max_files": MAX_FILES, "min_seeds": MIN_SEEDS,
                   "pkg_dirs": list(cfg["pkg_dirs"])},
        "n_commits": len(commits),
        "n_seeds": sum(len(c.seeds) for c in commits),
        "commits": [asdict(c) for c in commits],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path


def main() -> None:
    repos_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("evaluate/test_repos")
    out_dir = Path("runtime/eval/manifests")
    for name in TARGETS:
        path = mine(name, repos_dir, out_dir)
        data = json.loads(path.read_text())
        print(f"{name:>8}: {data['n_commits']:>4} commits  {data['n_seeds']:>4} seeds  -> {path}")


if __name__ == "__main__":
    main()
