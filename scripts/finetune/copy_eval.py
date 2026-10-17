"""Phase 2 copy evaluation (spec 2026-10-17-copy-phase2-design §3). Pure helpers + CLI.

    uv run python scripts/finetune/copy_eval.py drafts --split dev --out .local/finetune/copy/drafts/dev-base.json
    uv run python scripts/finetune/copy_eval.py drafts --split dev --copies .local/finetune/copy/teacher-dev.jsonl --out ...
    uv run python scripts/finetune/copy_eval.py pairs --new NEW.json --old OLD.json --name dev-2-1-v1
    uv run python scripts/finetune/copy_eval.py score --name dev-2-1-v1 [--min-win 0.7]
"""
import argparse
import json
import random
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
LABEL_HEADINGS = agent.LABEL_HEADINGS  # name-tag headings (spec §3.3); the runtime drops them too
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


RUBRIC = """채점 기준(위에서부터 차례로 따집니다. 앞 기준에서 차이가 분명하면 그 쪽이 이깁니다):
1. basis에 없는 제품 사실(기능·성능·소재·성분·인증·효능·관리법·수치)을 말하는 쪽이 집니다. 분위기·장면·느낌을 꾸미는 말은 제품 사실이 아닙니다.
2. 이 분류의 상품을 사고 싶어지게 하는가: 고객이 얻는 혜택이 분명한가, 다른 상품에도 붙일 수 있는 말이 아니라 이 상품만의 개성이 있는가.
3. 분류에 어울리는 말투인가.
4. 자연스러운 한국어인가: 반복, 베낀 듯한 상투구, 내용 없는 이름표 제목이 없는가.
모든 기준에서 비슷하면 tie입니다."""


def render(sections: list[dict]) -> str:
    return "\n".join(f"[{s['kind']}] {s['headline']}".rstrip() + (f"\n{s['body']}" if s.get("body") else "")
                     for s in sections)


def make_pairs(new: dict, old: dict, items: dict[str, dict], seed: int = 0) -> tuple[list[dict], dict]:
    """Blind A/B pairs (spec §3.4), new on a seeded random side. A draft that failed loses its product (a forfeit)."""
    def by_product(doc: dict) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for d in doc["drafts"]:
            if d["product_id"] in out:
                raise ValueError(f"{d['product_id']}: one draft per product for a blind pair")
            out[d["product_id"]] = d
        return out

    a, b = by_product(new), by_product(old)
    if set(a) != set(b):
        raise ValueError("new and old drafts cover different products")
    rng, pairs, sides, forfeits = random.Random(seed), [], {}, {}
    for pid in sorted(a):
        side = "A" if rng.random() < 0.5 else "B"
        n, o = a[pid], b[pid]
        if n["error"] or o["error"]:
            if not (n["error"] and o["error"]):
                forfeits[pid] = "new" if n["error"] else "old"
            continue
        first, second = (n, o) if side == "A" else (o, n)
        pairs.append({"id": pid, "category": items[pid]["category"], "basis": judge_data.basis(items[pid]),
                      "A": render(first["sections"]), "B": render(second["sections"])})
        sides[pid] = side
    return pairs, {"new_side": sides, "forfeits": forfeits}


def brief(pairs_path: Path, verdicts_path: Path) -> str:
    return f"""# 상세페이지 문구 블라인드 비교

`{pairs_path}`의 각 줄은 상품 하나입니다: id, category, basis(판매자 설명과 사진에서 보이는 것), A와 B(같은 상품의 두 문구).
줄마다 A와 B를 아래 기준으로 비교해 더 나은 쪽을 고르세요. 이 파일 말고는 아무 파일도 읽지 마세요.

{RUBRIC}

결과는 `{verdicts_path}`에 한 줄에 JSON 하나로 쓰세요: {{"id": "...", "winner": "A" | "B" | "tie", "reason": "한 줄 이유"}}.
모든 id에 답하세요. 끝나면 A·B·tie 개수만 보고하세요.
"""


def tally(verdicts: list[dict], key: dict) -> dict:
    sides, got = key["new_side"], {v["id"]: v["winner"] for v in verdicts}
    if set(got) != set(sides) or not set(got.values()) <= {"A", "B", "tie"}:
        raise ValueError("verdicts must answer every pair with A, B or tie")
    forfeits = list(key["forfeits"].values())
    wins = sum(w == sides[i] for i, w in got.items()) + forfeits.count("old")
    losses = sum(w not in (sides[i], "tie") for i, w in got.items()) + forfeits.count("new")
    ties = sum(w == "tie" for w in got.values())
    return {"wins": wins, "losses": losses, "ties": ties,
            "win_rate": round(wins / (wins + losses), 3) if wins + losses else 0.0}


def compare(new_m: dict, old_m: dict) -> dict[str, bool]:
    """Spec §3.3: each metric may get worse only by its tolerance."""
    ok = {k: new_m[k] - old_m[k] <= tolerance + 1e-9 for k, tolerance in TOLERANCE.items()}
    ok["thickness"] = new_m["thickness"] >= old_m["thickness"] * MIN_THICKNESS - 1e-9
    return ok


def cmd_pairs(args) -> int:
    key_path = ROOT / "keys" / f"{args.name}.json"
    if key_path.exists():  # a reshuffle after the verdicts would no longer match them
        raise SystemExit(f"{args.name} already has pairs; pick a new --name")
    new, old = (json.loads(path.read_text("utf-8")) for path in (args.new, args.old))
    pairs, key = make_pairs(new, old, products())
    blind = (ROOT / "blind" / args.name).resolve()
    blind.mkdir(parents=True, exist_ok=True)
    (blind / "pairs.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs), "utf-8")
    (blind / "brief.md").write_text(brief(blind / "pairs.jsonl", blind / "verdicts.jsonl"), "utf-8")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps({"new": str(args.new), "old": str(args.old), **key}, ensure_ascii=False, indent=1), "utf-8")
    print(f"{len(pairs)} pairs, {len(key['forfeits'])} forfeits → {blind / 'brief.md'}")
    return 0


def cmd_score(args) -> int:
    key = json.loads((ROOT / "keys" / f"{args.name}.json").read_text("utf-8"))
    new, old = (json.loads(Path(key[side]).read_text("utf-8")) for side in ("new", "old"))
    lines = (ROOT / "blind" / args.name / "verdicts.jsonl").read_text("utf-8").splitlines()
    blind = tally([json.loads(line) for line in lines if line.strip()], key)
    quoted = example_units(new["system"]) | example_units(old["system"])
    m_new, m_old = metrics(new["drafts"], quoted), metrics(old["drafts"], quoted)
    ok = compare(m_new, m_old)
    passed = blind["win_rate"] >= args.min_win and all(ok.values())
    print(f"blind: new {blind['wins']} / old {blind['losses']} / tie {blind['ties']} → win {blind['win_rate']} (≥ {args.min_win})")
    print("| metric | old | new | ok |\n|---|---|---|---|")
    for k in [*TOLERANCE, "thickness"]:
        print(f"| {k} | {m_old[k]} | {m_new[k]} | {'OK' if ok[k] else 'WORSE'} |")
    print(f"| failed | {m_old['failed']} | {m_new['failed']} | |")
    print(f"| seconds | {m_old['seconds']} | {m_new['seconds']} | ×{round(m_new['seconds'] / max(m_old['seconds'], 0.01), 2)} |")
    print("PASS" if passed else "FAIL")
    (ROOT / "keys" / f"{args.name}-score.json").write_text(json.dumps(
        {"blind": blind, "new": m_new, "old": m_old, "ok": ok, "passed": passed, "min_win": args.min_win},
        ensure_ascii=False, indent=1), "utf-8")
    return 0 if passed else 1


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
    p = sub.add_parser("pairs", help="블라인드 A/B 짝과 판정 지시문을 쓴다")
    p.add_argument("--new", type=Path, required=True)
    p.add_argument("--old", type=Path, required=True)
    p.add_argument("--name", required=True)
    s = sub.add_parser("score", help="판정을 열쇠로 풀고 기계 지표와 함께 합격 여부를 낸다")
    s.add_argument("--name", required=True)
    s.add_argument("--min-win", type=float, default=0.6)
    args = parser.parse_args(argv)
    return {"drafts": cmd_drafts, "pairs": cmd_pairs, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
