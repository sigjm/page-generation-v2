"""Score the claim judge on the reviewed eval set (spec 2026-10-16-judge-finetune §6).

    uv run python scripts/finetune/eval_judge.py --model empero-ai/Qwen3.8-9B-Distill-MLX-4bit --out .local/finetune/judge/eval-base.json

With LANGSMITH_API_KEY in .env the same predictions are also logged as a LangSmith experiment on the
dataset pagegen-judge-eval-<hash> (langchain-adoption spec §4), so base and fine-tuned runs sit side by side.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pagegen  # noqa: E402
from pagegen import agent, llm  # noqa: E402


def metrics(results: list[dict]) -> dict:
    tp = sum(r["gold"] and r["pred"] for r in results)
    fp = sum(not r["gold"] and r["pred"] for r in results)
    fn = sum(r["gold"] and not r["pred"] for r in results)
    clean = sum(not r["gold"] for r in results)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"n": len(results), "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "false_flag_rate": round(fp / clean, 3) if clean else 0.0, "failures": sum(r["failed"] for r in results)}


def served_models() -> list[str]:
    with urllib.request.urlopen(f"{llm.MLX_URL}/v1/models", timeout=10) as response:
        return [m["id"] for m in json.load(response)["data"]]


def dataset_name(rows: list[dict]) -> str:
    digest = hashlib.sha1(json.dumps([(r["id"], r["flagged"]) for r in rows]).encode()).hexdigest()[:8]
    return f"pagegen-judge-eval-{digest}"


def log_to_langsmith(rows: list[dict], results: list[dict], model: str) -> str | None:
    """Upload the eval set once and record these predictions as an experiment. None without a LangSmith key."""
    if not pagegen.enable_dev_tracing("pagegen-finetune"):
        return None
    from langsmith import Client, evaluate

    client, name = Client(), dataset_name(rows)
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name, description="pagegen claim-judge eval set (synthetic products, reviewed labels)")
        client.create_examples(dataset_id=dataset.id, examples=[
            {"inputs": {"id": r["id"], "text": r["text"], "basis": r["basis"]},
             "outputs": {"flagged": r["flagged"]}, "metadata": {"source": r["source"]}} for r in rows])
    by_id = {r["id"]: r for r in results}

    def target(inputs: dict) -> dict:  # predictions were already made above; no second model run
        r = by_id[inputs["id"]]
        return {"flagged": r["pred"], "failed": r["failed"]}

    def correct(outputs: dict, reference_outputs: dict) -> dict:
        return {"key": "correct", "score": int(outputs["flagged"] == reference_outputs["flagged"])}

    run = evaluate(target, data=name, evaluators=[correct], experiment_prefix=f"judge-{model.split('/')[-1]}",
                   metadata={"model": model, **metrics(results)}, max_concurrency=4)
    return run.experiment_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--eval", type=Path, default=Path(".local/finetune/judge/eval.jsonl"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.model not in served_models():  # MLX Serve answers an unknown name with whatever model is loaded
        raise SystemExit(f"MLX Serve does not list {args.model}; restart the MLX-Serve app after a fuse")
    llm.JUDGE_MODEL = args.model
    rows = [json.loads(line) for line in args.eval.read_text("utf-8").splitlines() if line.strip()]
    results = []
    for row in rows:
        started, failed = time.perf_counter(), False
        try:
            pred = bool(agent.judge(llm, row["text"], row["basis"]))
        except llm.LLMError:
            pred, failed = True, True  # runtime is fail-closed: a failed judge drops the sentence
        results.append({"id": row["id"], "text": row["text"], "gold": row["flagged"], "pred": pred, "failed": failed,
                        "seconds": round(time.perf_counter() - started, 2)})
    m = metrics(results) | {"model": args.model, "avg_seconds": round(sum(r["seconds"] for r in results) / len(results), 2)}
    print(json.dumps(m, ensure_ascii=False))
    for r in results:
        if r["gold"] != r["pred"]:
            print(f"{'MISS' if r['gold'] else 'FALSE'} {r['text']}")
    experiment = log_to_langsmith(rows, results, args.model)
    if experiment:
        print(f"LangSmith experiment: {experiment}")
    if args.out:
        args.out.write_text(json.dumps({"metrics": m, "experiment": experiment, "results": results},
                                       ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
