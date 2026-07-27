"""Does the shipped record/augment path reproduce the measured improvement?"""
import statistics
from pathlib import Path
from code_review_graph.graph import GraphStore
from code_review_graph.incremental import full_build, get_db_path
from code_review_graph.postprocessing import run_post_processing
from runtime.augment import clear, materialize
from runtime.eval.augmented_eval import seeds_for
from runtime.eval.ranking_union import union_rr, summarize


def main():
    repo = Path("evaluate/test_repos/httpx").resolve()
    store = GraphStore(get_db_path(repo))
    full_build(repo, store)
    run_post_processing(store)
    seeds = seeds_for("httpx", repo)

    n = clear(store)
    base = summarize(union_rr(store, seeds))
    print(f"cleared {n} runtime edges")
    print("static graph   :", base)

    print("augment        :", materialize(store))
    aug = summarize(union_rr(store, seeds))
    print("augmented graph:", aug)
    print(f"\nMRR {base['MRR']} -> {aug['MRR']}  "
          f"({100*(aug['MRR']-base['MRR'])/base['MRR']:+.1f}%)")
    store.close()


if __name__ == "__main__":
    main()
