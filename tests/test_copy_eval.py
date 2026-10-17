import json

import pytest

from scripts.finetune import copy_eval as ce

PRODUCT = {"id": "food-001", "category": "식품", "name": "감귤 주스", "notes": "제주 감귤 100%, 1L.",
           "visible_features": ["유리병"], "colors": ["주황"]}
COPY = {"hero": {"headline": "아침을 깨우는 상큼함", "body": "제주 감귤 100% 주스예요. 최고의 맛."},
        "banner": {"headline": "아침이 무겁나요?"},
        "points": {"headline": "주요 특징", "items": [{"title": "제주 감귤", "text": "제주 감귤 100%."},
                                                    {"title": "넉넉함", "text": "1L 한 병."},
                                                    {"title": "상큼함", "text": "새콤달콤해요."}]},
        "scene": {"headline": "햇살 드는 식탁", "body": "", "photo_instruction": "햇살 드는 식탁 위"},
        "notice": {"headline": "", "body": ""}, "details": []}


class Judge:
    """Stand-in for pagegen.llm in the claim check: flags a sentence containing a `bad` word."""
    JUDGE_MODEL = "judge"

    def __init__(self, bad=()):
        self.bad = bad

    def chat_json(self, messages, schema, **kw):
        text = messages[1]["content"].split("새 문구: ", 1)[1]
        if any(b in text for b in self.bad):
            return {"phrases": [{"phrase": text, "type": "fact", "supported": False}]}
        return {"phrases": []}


def test_splits_follow_the_spec_per_category():
    items = {f"{c}-{i:03d}": {"category": c} for c in ("food", "living") for i in range(1, 61)}
    s = ce.splits(items)
    assert s["dev"] == [f"{c}-{i:03d}" for c in ("food", "living") for i in range(1, 7)]
    assert s["test"] == [f"{c}-{i:03d}" for c in ("food", "living") for i in range(13, 19)]
    assert len(s["train"]) == 96 and not set(s["train"]) & set(s["dev"] + s["test"])


def test_messages_are_the_runtime_copy_prompt_without_photos():
    from pagegen import prompts
    system, user = ce.messages(PRODUCT)
    assert system == {"role": "system", "content": prompts.COPY_SYSTEM}
    assert user["content"] == ('상품명: 감귤 주스\n판매자 설명: 제주 감귤 100%, 1L.\n사진 분석: {"category": "식품", '
                               '"visible_features": ["유리병"], "colors": ["주황"], "facts": [], "questions": [], '
                               '"photo_captions": []}\n사진 설명 없음: details는 빈 목록.')


def test_draft_runs_the_runtime_checks_and_keeps_what_was_dropped():
    d = ce.draft("food-001", PRODUCT, lambda p: COPY, Judge(bad=["새콤"]))
    assert d["error"] is None and d["copy"] == COPY and d["product_id"] == "food-001"
    assert {x["reason"] for x in d["dropped"]} == {"superlative", "judge"}
    assert "새콤달콤" not in json.dumps(d["sections"], ensure_ascii=False)  # the flagged point is gone
    assert (d["sentences"], d["kept"]) == (9, 7)


def test_draft_records_a_model_failure():
    def down(product):
        raise ce.llm.LLMError("모델 응답을 두 번 받지 못했습니다")
    d = ce.draft("food-001", PRODUCT, down, Judge())
    assert d["error"].startswith("모델 응답") and d["copy"] is None and d["kept"] == 0


def test_resume_refuses_a_file_made_with_another_prompt_or_source(tmp_path):
    out = tmp_path / "d.json"
    header = {"split": "dev", "source": "model:m", "system": "새 프롬프트"}
    assert ce.resume(out, header) == {**header, "drafts": []}
    out.write_text(json.dumps({**header, "system": "옛 프롬프트", "drafts": []}), "utf-8")
    with pytest.raises(SystemExit, match="--out"):
        ce.resume(out, header)
    out.write_text(json.dumps({**header, "drafts": [{"id": "a", "error": None}, {"id": "b", "error": "down"}]}), "utf-8")
    assert [d["id"] for d in ce.resume(out, header)["drafts"]] == ["a"]  # failed drafts are made again


def test_drafts_refuse_a_model_the_server_does_not_list(monkeypatch, tmp_path):
    # MLX Serve answers an unknown model name with whatever model is loaded
    monkeypatch.setattr(ce.eval_judge, "served_models", lambda: ["empero-ai/base"])
    with pytest.raises(SystemExit, match="local/new"):
        ce.main(["drafts", "--split", "dev", "--model", "local/new", "--out", str(tmp_path / "d.json")])


def test_metrics_measure_copying_repeats_labels_hanja_drops_and_thickness():
    quoted = ce.example_units('예) "햇살 드는 식탁 위", "주요 특징"')
    assert quoted == {"햇살 드는", "드는 식탁", "식탁 위", "주요 특징"}
    base = {"error": None, "dropped": [], "sentences": 10, "kept": 8, "seconds": 2.0}
    drafts = [{**base, "copy": COPY, "dropped": [{"text": "x", "reason": "judge"}]},
              {**base, "copy": {**COPY, "hero": {"headline": "上品한 맛", "body": ""}}},
              {**base, "copy": COPY},
              {**base, "copy": None, "error": "down", "sentences": 0, "kept": 0}]
    m = ce.metrics(drafts, quoted)
    assert (m["drafts"], m["failed"]) == (3, 1)
    assert m["example_copy"] == 1.0  # every draft has "주요 특징" and "햇살 드는"
    assert m["duplicates"] == 0.72  # 18 of 25 sentence occurrences are shared by all three drafts
    assert m["label_headings"] == 0.25  # "주요 특징": 3 of 12 headings
    assert (m["hanja"], m["dropped"], m["thickness"]) == (1, 0.033, 8.0)


def _doc(drafts, system="프롬프트"):
    return {"split": "dev", "source": "model:m", "system": system, "drafts": drafts}


def _made(pid, headline, error=None):
    return {"id": pid, "product_id": pid, "copy": COPY, "error": error, "dropped": [], "sentences": 2, "kept": 2,
            "seconds": 1.0, "sections": [] if error else [{"kind": "hero", "headline": headline, "body": "본문"}]}


def test_pairs_hide_which_side_is_new_and_the_key_unmasks_it():
    items = {f"food-00{i}": {**PRODUCT, "id": f"food-00{i}"} for i in range(1, 9)}
    new, old = _doc([_made(pid, "새 문구") for pid in items]), _doc([_made(pid, "옛 문구") for pid in items])
    pairs, key = ce.make_pairs(new, old, items)
    assert {p["id"] for p in pairs} == set(items) and set(key["new_side"].values()) == {"A", "B"}
    for p in pairs:
        assert set(p) == {"id", "category", "basis", "A", "B"}
        assert "새 문구" in p[key["new_side"][p["id"]]] and p["basis"].startswith("근거 텍스트: 감귤 주스")
    assert ce.make_pairs(new, old, items) == (pairs, key)  # fixed seed


def test_a_failed_draft_is_a_forfeit_not_a_pair():
    items = {pid: {**PRODUCT, "id": pid} for pid in ("food-001", "food-002", "food-003")}
    new = _doc([_made("food-001", "새"), _made("food-002", "", error="down"), _made("food-003", "", error="down")])
    old = _doc([_made("food-001", "옛"), _made("food-002", "옛"), _made("food-003", "", error="down")])
    pairs, key = ce.make_pairs(new, old, items)
    assert [p["id"] for p in pairs] == ["food-001"] and key["forfeits"] == {"food-002": "new"}


def test_score_counts_wins_ties_and_forfeits():
    key = {"new_side": {"a": "A", "b": "B", "c": "A", "d": "B"}, "forfeits": {"e": "old", "f": "new", "g": "old"}}
    verdicts = [{"id": "a", "winner": "A"}, {"id": "b", "winner": "A"}, {"id": "c", "winner": "tie"},
                {"id": "d", "winner": "B"}]
    assert ce.tally(verdicts, key) == {"wins": 4, "losses": 2, "ties": 1, "win_rate": 0.667}
    with pytest.raises(ValueError):
        ce.tally(verdicts[:3], key)  # every pair needs a verdict
    with pytest.raises(ValueError):
        ce.tally([*verdicts[:3], {"id": "d", "winner": "비슷함"}], key)


def test_compare_allows_the_spec_tolerances_only():
    old = {"example_copy": 0.5, "duplicates": 0.1, "label_headings": 0.2, "hanja": 3, "dropped": 0.1, "thickness": 10.0}
    assert all(ce.compare({**old, "example_copy": 0.52, "hanja": 4, "thickness": 9.0}, old).values())
    worse = ce.compare({**old, "duplicates": 0.13, "hanja": 5, "thickness": 8.9}, old)
    assert [k for k, ok in worse.items() if not ok] == ["duplicates", "hanja", "thickness"]


def test_brief_gives_the_judge_the_rubric_and_the_pairs_only(tmp_path):
    text = ce.brief(tmp_path / "pairs.jsonl", tmp_path / "verdicts.jsonl")
    assert ce.RUBRIC in text and str(tmp_path / "pairs.jsonl") in text and str(tmp_path / "verdicts.jsonl") in text
    assert "key" not in text


def test_pairs_refuse_to_reshuffle_a_named_comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "ROOT", tmp_path)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "gate.json").write_text("{}", "utf-8")
    with pytest.raises(SystemExit, match="gate"):
        ce.main(["pairs", "--new", "n.json", "--old", "o.json", "--name", "gate"])


def test_prompts_quote_no_dev_or_test_product():
    from pathlib import Path

    from pagegen import prompts
    from scripts.finetune import codex_teacher as ct
    path = Path(".local/finetune/judge/products.jsonl")
    if not path.exists():
        pytest.skip("products.jsonl lives only where the data is made")
    items = {p["id"]: p for p in map(json.loads, path.read_text("utf-8").splitlines())}
    held = ce.splits(items)
    names = [items[pid]["name"] for pid in held["dev"] + held["test"]]
    constants = [prompts.COPY_SYSTEM] + [v for v in vars(ct).values() if isinstance(v, str)]
    assert [n for n in names if any(n in c for c in constants)] == []
