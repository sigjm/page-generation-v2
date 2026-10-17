"""Phase 2 copy evaluation (spec 2026-10-17-copy-phase2-design §3). Pure helpers + CLI.

    uv run python scripts/finetune/copy_eval.py drafts --split dev --out .local/finetune/copy/drafts/dev-base.json
    uv run python scripts/finetune/copy_eval.py drafts --split dev --copies .local/finetune/copy/teacher-dev.jsonl --out ...
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pagegen import agent, llm, prompts  # noqa: E402
from pagegen import page as pg  # noqa: E402
from scripts.finetune import eval_judge, judge_data  # noqa: E402  (judge_data turns on dev tracing: synthetic data only)

ROOT = Path(".local/finetune/copy")
NO_DETAILS = "사진 설명 없음: details는 빈 목록."
PHOTOS = [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"}]  # one photo: no splits, no details, no gallery
# Name-tag headings (spec §3.3), from the baseline drafts; the notice's own headings are allowed and not counted
LABEL_HEADINGS = ("주요 특징", "제품 특징", "이래서 좋아요", "이런 점이 좋아요", "이런 점이 좋습니다", "이런 점이 특별해요",
                  "이런 점이 특별합니다", "사용 방법", "사용 전 확인하세요", "사용 전 꼭 확인하세요", "사용 전 확인해 주세요",
                  "구매 전 확인하세요", "구매 전 확인해 주세요", "관리 방법", "보관 방법", "세탁 방법", "관리 및 주의 사항",
                  "관리와 주의 사항", "관리 및 주의", "관리 및 보관 방법")
TOLERANCE = {"example_copy": 0.02, "duplicates": 0.02, "label_headings": 0.02, "hanja": 1, "dropped": 0.02}
MIN_THICKNESS = 0.9  # the new drafts keep at least 90% of the old sentence count


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).rstrip(".!?")


_LABELS = {_norm(h) for h in LABEL_HEADINGS}


def products() -> dict[str, dict]:
    return {p["id"]: p for p in judge_data.read("products.jsonl")}


def splits(products: dict[str, dict]) -> dict[str, list[str]]:
    """Spec §3.1, per category in id order: dev = 1st–6th, test = 13th–18th, train = the rest (7th–12th included)."""
    by_category: dict[str, list[str]] = {}
    for pid, p in sorted(products.items()):
        by_category.setdefault(p["category"], []).append(pid)
    out: dict[str, list[str]] = {"dev": [], "test": [], "train": []}
    for ids in by_category.values():
        out["dev"] += ids[:6]
        out["test"] += ids[12:18]
        out["train"] += ids[6:12] + ids[18:]
    return out


def inputs(product: dict) -> tuple[str, dict, str]:
    """(seller, analysis, hint) the way the runtime builds them, without photos (as judge_data.cmd_sentences)."""
    analysis = {"category": product["category"], "visible_features": product["visible_features"],
                "colors": product["colors"], "facts": [], "questions": [], "photo_captions": []}
    return f"상품명: {product['name']}\n판매자 설명: {product['notes']}", analysis, NO_DETAILS


def messages(product: dict) -> list[dict]:
    return agent.copy_messages(*inputs(product))


def _texts(sections: list[dict]) -> int:
    """What _clean checks one by one: each headline whole, each body sentence by sentence."""
    return sum(bool(s["headline"].strip()) + sum(len(agent._sentences(line)) for line in s["body"].splitlines())
               for s in sections if s["kind"] not in ("spec_table", "gallery"))


def draft(draft_id: str, product: dict, copy_of, judge_llm=llm) -> dict:
    """One draft through the runtime assembly and checks (spec §3.2). A model or judge failure is recorded, not raised."""
    started = time.perf_counter()
    copy, error, result = None, None, {"sections": [], "dropped": [], "sentences": 0, "kept": 0}
    try:
        copy = copy_of(product)
        sections, _ = agent.build_sections(copy, inputs(product)[1], product["name"], PHOTOS)
        sections = agent._settle(sections)
        written = _texts(sections)
        kept, tally = agent.check_sections(sections, f"{product['name']}\n{product['notes']}",
                                           judge_data.basis(product), product["name"], judge_llm)
        result = {"sections": kept, "dropped": tally["dropped"], "sentences": written, "kept": _texts(kept)}
    except llm.LLMError as e:
        error = str(e)
    return {"id": draft_id, "product_id": product["id"], "copy": copy, **result,
            "seconds": round(time.perf_counter() - started, 2), "error": error}


def resume(out: Path, header: dict) -> dict:
    """The drafts file to continue: finished drafts stay, failed ones are made again.
    A file made with another prompt, source or split is refused: mixing them would compare nothing."""
    if not out.exists():
        return {**header, "drafts": []}
    doc = json.loads(out.read_text("utf-8"))
    if any(doc.get(key) != value for key, value in header.items()):
        raise SystemExit(f"{out} was made with another prompt, source or split; use a new --out")
    return {**doc, "drafts": [d for d in doc["drafts"] if d["error"] is None]}


def save(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")


def example_units(system: str) -> set[str]:
    """Spec §3.3 example copying: each quoted example of a copy prompt, or any two words in a row of a longer one
    (the baseline's "아침 햇살이 드는 원목 식탁 위" came back as "아침 햇살이 드는 주방")."""
    units: set[str] = set()
    for quote in re.findall(r'"([^"\n]+)"', system):
        words = quote.split()
        units |= {" ".join(words[i:i + 2]) for i in range(len(words) - 1)} if len(words) > 2 else {quote.strip()}
    return {u for u in units if u}


def _strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def _headings(copy: dict) -> list[str]:
    """Model-written section headings outside the notice."""
    parts = [copy.get(name) if isinstance(copy.get(name), dict) else {} for name in ("hero", "banner", "points", "scene")]
    found = [p.get("headline", "") for p in parts] + [d.get("headline", "") for d in copy.get("details") or []
                                                        if isinstance(d, dict)]
    return [str(h).strip() for h in found if str(h).strip()]


def metrics(drafts: list[dict], quoted: set[str]) -> dict:
    """Spec §3.3 over the drafts that were made; `quoted` is the union of both prompts' example_units."""
    ok = [d for d in drafts if d["error"] is None]
    n = len(ok) or 1
    per_draft = [set(judge_data.copy_sentences(d["copy"])) for d in ok]
    shared = Counter(s for sentences in per_draft for s in sentences)
    occurrences = sum(len(sentences) for sentences in per_draft) or 1
    heads = [h for d in ok for h in _headings(d["copy"])]
    return {
        "drafts": len(ok), "failed": len(drafts) - len(ok),
        "example_copy": round(sum(any(q in s for s in _strings(d["copy"]) for q in quoted) for d in ok) / n, 3),
        "duplicates": round(sum(shared[s] >= 3 for sentences in per_draft for s in sentences) / occurrences, 3),
        "label_headings": round(sum(_norm(h) in _LABELS for h in heads) / (len(heads) or 1), 3),
        "hanja": sum(bool(pg._HANJA.search(json.dumps(d["copy"], ensure_ascii=False))) for d in ok),
        "dropped": round(sum(len(d["dropped"]) for d in ok) / (sum(d["sentences"] for d in ok) or 1), 3),
        "thickness": round(sum(d["kept"] for d in ok) / n, 2),
        "seconds": round(sum(d["seconds"] for d in ok) / n, 2),
    }


def cmd_drafts(args) -> int:
    if args.copies:
        source = f"copies:{args.copies}"
    else:
        if args.model not in eval_judge.served_models():  # MLX Serve answers an unknown name with the loaded model
            raise SystemExit(f"MLX Serve does not list {args.model}; restart the MLX-Serve app after a fuse")
        source = f"model:{args.model}"
    doc = resume(args.out, {"split": args.split, "source": source, "system": prompts.COPY_SYSTEM})
    items = products()
    ids = set(splits(items)[args.split])
    if args.copies:
        rows = [json.loads(line) for line in args.copies.read_text("utf-8").splitlines() if line.strip()]
        todo = [(r["id"], r["product_id"], lambda p, c=r["copy"]: c) for r in rows if r["product_id"] in ids]
    else:
        todo = [(pid, pid, lambda p: llm.chat_json(messages(p), prompts.COPY_SCHEMA, model=args.model))
                for pid in sorted(ids)]
    done = {d["id"] for d in doc["drafts"]}
    todo = [t for t in todo if t[0] not in done]
    with ThreadPoolExecutor(args.workers) as pool:
        for n, made in enumerate(pool.map(lambda t: draft(t[0], items[t[1]], t[2]), todo), 1):
            doc["drafts"].append(made)
            if n % 10 == 0:
                save(args.out, doc)
                print(f"{n}/{len(todo)} drafts", flush=True)
    save(args.out, doc)
    print(json.dumps(metrics(doc["drafts"], example_units(doc["system"])), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("drafts", help="초안을 만들고 런타임 검사를 거친다")
    d.add_argument("--split", choices=["dev", "test", "train"], required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--model", default=llm.COPY_MODEL)
    d.add_argument("--copies", type=Path, help="교사 초안 JSONL. 주면 모델을 부르지 않고 검사만 한다")
    d.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    return {"drafts": cmd_drafts}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
