"""Claim-judge fine-tuning data (spec 2026-10-16-judge-finetune §3). Pure helpers + CLI.

    uv run python scripts/finetune/judge_data.py sentences   # 9B writes copy for every product (needs MLX Serve)
    uv run python scripts/finetune/judge_data.py build       # validate labels, split, write sft/ and eval_candidates
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pagegen  # noqa: E402
from pagegen import agent, llm, prompts  # noqa: E402

pagegen.enable_dev_tracing("pagegen-finetune")  # synthetic data only (langchain-adoption spec §4)
from pagegen.page import is_sourced  # noqa: E402

ROOT = Path(".local/finetune/judge")
HELD_CATEGORY_COUNT = 6  # products per category kept out of training for the eval set
MAX_FLAGGED = 0.45  # spec §3.2: 35-50% of training sentences carry an unsupported claim


def read(name: str) -> list[dict]:
    file = ROOT / name
    return [json.loads(line) for line in file.read_text("utf-8").splitlines() if line.strip()] if file.exists() else []


def write(name: str, rows: list[dict]) -> None:
    (ROOT / name).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def basis(product: dict) -> str:
    page = {"product": {"name": product["name"], "notes": product["notes"]},
            "analysis": {"visible_features": product["visible_features"], "colors": product["colors"]}}
    return agent.basis_of(page, [])


def copy_sentences(copy: dict) -> list[str]:
    """Headline and body sentences in page order, split the way the runtime judges them."""
    def part(name):
        value = copy.get(name)
        return value if isinstance(value, dict) else {}

    texts = [part("hero").get("headline", ""), part("hero").get("body", ""), part("banner").get("headline", ""),
             part("points").get("headline", ""),
             *(f"{i.get('title', '')}: {i.get('text', '')}" for i in part("points").get("items") or [] if isinstance(i, dict)),
             *(f"{d.get('headline', '')}\n{d.get('body', '')}" for d in copy.get("details") or [] if isinstance(d, dict)),
             part("scene").get("headline", ""), part("scene").get("body", ""),
             part("notice").get("headline", ""), part("notice").get("body", "")]
    return [s for text in texts for line in str(text).splitlines() for s in agent._sentences(line)]


def validate_label(text: str, label: dict, injected: str | None = None) -> dict | None:
    """The label if it passes the mechanical checks (spec §3.3), else None."""
    phrases = label.get("phrases")
    if not isinstance(phrases, list) or not phrases:
        return None
    seen = set()
    for p in phrases:
        if not isinstance(p, dict) or p.get("type") not in ("fact", "mood") or not isinstance(p.get("supported"), bool):
            return None
        phrase = str(p.get("phrase") or "")
        if not phrase or not is_sourced(phrase, text) or phrase in seen:
            return None
        seen.add(phrase)
    if injected and not any(p["type"] == "fact" and not p["supported"] and
                            (is_sourced(injected, p["phrase"]) or is_sourced(p["phrase"], injected))
                            for p in phrases):
        return None
    return {"phrases": [{"phrase": p["phrase"], "type": p["type"], "supported": p["supported"]} for p in phrases]}


def flagged(label: dict) -> bool:
    return any(p["type"] != "mood" and p["supported"] is not True for p in label["phrases"])


def split(product_ids: list[str], held_per) -> tuple[list[str], list[str], list[str]]:
    held = [p for p in product_ids if held_per(p)]
    rest = [p for p in product_ids if not held_per(p)]
    valid = [p for p in rest if int(hashlib.sha1(p.encode()).hexdigest(), 16) % 10 == 0]
    return held, [p for p in rest if p not in valid], valid


def cap_flagged(rows: list[tuple[dict, bool]], cap: float = MAX_FLAGGED) -> list[dict]:
    """At most `cap` of the rows flagged: drops flagged 9B rows at random; injected rows (id ...-inj) all stay."""
    clean = sum(not f for _, f in rows)
    allowed = round(cap * clean / (1 - cap))
    natural = [r for r, f in rows if f and not r["id"].endswith("-inj")]
    extra = sum(f for _, f in rows) - allowed
    if extra <= 0:
        return [r for r, _ in rows]
    dropped = {r["id"] for r in random.Random(0).sample(natural, min(extra, len(natural)))}
    return [r for r, _ in rows if r["id"] not in dropped]


def sft_row(sentence_id: str, product: dict, text: str, label: dict) -> dict:
    answer = json.dumps({"phrases": label["phrases"]}, ensure_ascii=False, separators=(",", ":"))
    return {"id": sentence_id, "messages": [*agent.judge_messages(text, basis(product)),
                                            {"role": "assistant", "content": answer}]}


def cmd_sentences(workers: int) -> int:
    from concurrent.futures import ThreadPoolExecutor

    products, done = read("products.jsonl"), {r["product_id"] for r in read("sentences.jsonl")}

    def one(product: dict) -> list[dict]:
        analysis = {"category": product["category"], "visible_features": product["visible_features"],
                    "colors": product["colors"], "facts": [], "questions": [], "photo_captions": []}
        seller = f"상품명: {product['name']}\n판매자 설명: {product['notes']}"
        messages = [{"role": "system", "content": prompts.COPY_SYSTEM},
                    {"role": "user", "content": f"{seller}\n사진 분석: {json.dumps(analysis, ensure_ascii=False)}\n사진 설명 없음: details는 빈 목록."}]
        rows, seen = [], set()
        for tag, temperature in (("9b-t0", 0.0), ("9b-t07", 0.7)):
            try:
                copy = llm.parse_json(llm.chat(messages, schema=prompts.COPY_SCHEMA, max_tokens=1500,
                                               temperature=temperature).get("content"))
            except (llm.LLMError, ValueError):
                continue
            for i, text in enumerate(copy_sentences(copy)):
                if text not in seen:
                    seen.add(text)
                    rows.append({"id": f"{product['id']}-{tag}-{i:02d}", "product_id": product["id"], "text": text, "source": tag})
        return rows

    todo = [p for p in products if p["id"] not in done]
    out = read("sentences.jsonl")
    with ThreadPoolExecutor(workers) as pool:
        for n, rows in enumerate(pool.map(one, todo), 1):
            out.extend(rows)
            if n % 10 == 0:
                write("sentences.jsonl", out)
                print(f"{n}/{len(todo)} products, {len(out)} sentences", flush=True)
    write("sentences.jsonl", out)
    print(f"done: {len(out)} sentences from {len({r['product_id'] for r in out})} products")
    return 0


def cmd_build() -> int:
    products = {p["id"]: p for p in read("products.jsonl")}
    sentences = {r["id"]: r for r in read("sentences.jsonl") + read("injected.jsonl")}
    labels = {r["id"]: r for r in read("labels.jsonl")}
    by_category: dict[str, list[str]] = {}
    for pid, p in sorted(products.items()):
        by_category.setdefault(p["category"], []).append(pid)
    held_ids = {pid for ids in by_category.values() for pid in ids[:HELD_CATEGORY_COUNT]}
    held, train, valid = split(sorted(products), held_per=lambda pid: pid in held_ids)
    rows: dict[str, list[dict]] = {"train": [], "valid": [], "held": []}
    is_flagged: dict[str, bool] = {}
    dropped = 0
    for sid, s in sentences.items():
        label = validate_label(s["text"], labels.get(sid, {}), s.get("injected_phrase"))
        if label is None:
            dropped += 1
            continue
        bucket = "held" if s["product_id"] in held else "valid" if s["product_id"] in valid else "train"
        if bucket == "held":
            rows["held"].append({"id": sid, "product_id": s["product_id"], "text": s["text"], "source": s["source"],
                                 "basis": basis(products[s["product_id"]]), **label, "flagged": flagged(label)})
        else:
            rows[bucket].append(sft_row(sid, products[s["product_id"]], s["text"], label))
            is_flagged[sid] = flagged(label)
    before = len(rows["train"])
    rows["train"] = cap_flagged([(r, is_flagged[r["id"]]) for r in rows["train"]])
    random.Random(0).shuffle(rows["train"])
    write("sft/train.jsonl", rows["train"])
    write("sft/validation.jsonl", rows["valid"])
    held_flagged = [r for r in rows["held"] if r["flagged"]]
    held_clean = [r for r in rows["held"] if not r["flagged"]]
    rng = random.Random(0)
    write("eval_candidates.jsonl", rng.sample(held_flagged, min(75, len(held_flagged))) + rng.sample(held_clean, min(75, len(held_clean))))
    train_flagged = sum(is_flagged[r["id"]] for r in rows["train"])
    print(json.dumps({"train": len(rows["train"]), "validation": len(rows["valid"]), "held": len(rows["held"]),
                      "dropped": dropped, "capped": before - len(rows["train"]), "train_flagged_ratio": round(train_flagged / max(1, len(rows["train"])), 3)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sentences", "build"])
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    return cmd_sentences(args.workers) if args.command == "sentences" else cmd_build()


if __name__ == "__main__":
    sys.exit(main())
