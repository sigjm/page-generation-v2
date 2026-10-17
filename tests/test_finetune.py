import json

from scripts.finetune import judge_data as jd

PRODUCT = {"id": "food-001", "category": "식품", "name": "감귤 주스", "notes": "제주 감귤 100%, 1L.",
           "visible_features": ["유리병"], "colors": ["주황"]}


def label(*phrases):
    return {"id": "x", "phrases": [{"phrase": p, "type": t, "supported": s} for p, t, s in phrases]}


def test_basis_matches_the_runtime_format():
    assert jd.basis(PRODUCT) == "근거 텍스트: 감귤 주스\n제주 감귤 100%, 1L.\n사진에서 보이는 것: 유리병, 주황"


def test_copy_sentences_split_like_the_runtime():
    copy = {"hero": {"headline": "아침의 상큼함", "body": "제주 감귤을 담았어요. 매일 한 잔."},
            "banner": {"headline": "아침이 무겁나요?"}, "points": {"headline": "이래서 좋아요", "items": [{"title": "상큼", "text": "새콤달콤해요."}]},
            "scene": {"headline": "", "body": ""}, "notice": {"headline": "보관", "body": "냉장 보관하세요."}, "details": []}
    assert jd.copy_sentences(copy) == ["아침의 상큼함", "제주 감귤을 담았어요.", "매일 한 잔.", "아침이 무겁나요?",
                                       "이래서 좋아요", "상큼: 새콤달콤해요.", "보관", "냉장 보관하세요."]


def test_validate_label_rejects_phrase_not_in_text():
    assert jd.validate_label("새콤달콤해요.", label(("새콤 달콤", "mood", True))) is not None  # whitespace-insensitive match
    assert jd.validate_label("새콤달콤해요.", label(("상큼해요", "mood", True))) is None


def test_validate_label_rejects_empty_and_duplicates():
    assert jd.validate_label("새콤달콤해요.", label()) is None
    assert jd.validate_label("새콤달콤해요.", label(("새콤", "mood", True), ("새콤", "mood", True))) is None


def test_validate_label_requires_injected_phrase_flagged():
    text = "새콤달콤하고 면역력을 높여줘요."
    ok = label(("새콤달콤하고", "mood", True), ("면역력을 높여줘요", "fact", False))
    bad = label(("새콤달콤하고", "mood", True), ("면역력을 높여줘요", "fact", True))
    assert jd.validate_label(text, ok, injected="면역력을 높여줘요") is not None
    assert jd.validate_label(text, bad, injected="면역력을 높여줘요") is None


def test_flagged_means_an_unsupported_fact():
    assert jd.flagged(label(("면역력", "fact", False)))
    assert not jd.flagged(label(("새콤", "mood", True), ("감귤", "fact", True)))


def test_split_is_by_product():
    ids = [f"p{i:03d}" for i in range(300)]
    held, train, valid = jd.split(ids, held_per=lambda pid: pid < "p030")
    assert set(held) == {f"p{i:03d}" for i in range(30)}
    assert not (set(train) & set(valid)) and not (set(held) & (set(train) | set(valid)))
    assert 0.05 < len(valid) / (len(train) + len(valid)) < 0.15


def test_sft_row_uses_runtime_messages_and_compact_json():
    row = jd.sft_row("s1", PRODUCT, "냉장 보관하세요.", label(("냉장 보관하세요", "fact", False)))
    system, user, assistant = row["messages"]
    assert user["content"].endswith("\n새 문구: 냉장 보관하세요.") and user["content"].startswith("근거 텍스트: 감귤 주스")
    assert assistant["content"] == '{"phrases":[{"phrase":"냉장 보관하세요","type":"fact","supported":false}]}'


from scripts.finetune import eval_judge as ej


def test_metrics_count_failures_as_flagged():
    results = [{"gold": True, "pred": True, "failed": False}, {"gold": True, "pred": False, "failed": False},
               {"gold": False, "pred": False, "failed": False}, {"gold": False, "pred": True, "failed": True}]
    m = ej.metrics(results)
    assert (m["precision"], m["recall"]) == (0.5, 0.5)
    assert m["f1"] == 0.5 and m["false_flag_rate"] == 0.5 and m["failures"] == 1


def test_eval_refuses_a_model_the_server_does_not_list(monkeypatch, tmp_path):
    import sys

    import pytest

    # MLX Serve answers an unknown model name with whatever model is loaded, so the eval would score that one
    monkeypatch.setattr(ej, "served_models", lambda: ["empero-ai/base"])
    monkeypatch.setattr(ej.agent, "judge", lambda *a: pytest.fail("judged with an unlisted model"))
    (tmp_path / "eval.jsonl").write_text('{"id": "a", "text": "t", "basis": "b", "flagged": true}\n', "utf-8")
    monkeypatch.setattr(sys, "argv", ["eval_judge.py", "--model", "local/new", "--eval", str(tmp_path / "eval.jsonl")])
    with pytest.raises(SystemExit, match="local/new"):
        ej.main()


def test_langsmith_logging_needs_a_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert ej.log_to_langsmith([], [], "m") is None


def test_dataset_name_changes_with_the_labels():
    a = [{"id": "x", "flagged": True}]
    b = [{"id": "x", "flagged": False}]
    assert ej.dataset_name(a) != ej.dataset_name(b) and ej.dataset_name(a).startswith("pagegen-judge-eval-")


def test_cap_flagged_drops_9b_flagged_rows_and_keeps_injected():
    rows = ([({"id": f"c{i}"}, False) for i in range(55)] + [({"id": f"f{i}"}, True) for i in range(40)]
            + [({"id": f"x{i}-inj"}, True) for i in range(10)])
    kept = jd.cap_flagged(rows, cap=0.45)
    ids = {r["id"] for r in kept}
    assert {f"c{i}" for i in range(55)} <= ids and {f"x{i}-inj" for i in range(10)} <= ids
    assert len(kept) == 100  # 45 flagged of 100
    assert jd.cap_flagged(rows[:60], cap=0.45) == [r for r, _ in rows[:60]]  # already under the cap


def test_labeling_injected_sentences_keeps_the_original_labels(tmp_path, monkeypatch):
    from scripts.finetune import codex_teacher as ct

    def put(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
        return path

    original = {"id": "food-001-9b-t0-00", "product_id": "food-001", "text": "상큼한 감귤 주스", "source": "9b-t0"}
    injected = {"id": "food-001-9b-t0-00-inj", "product_id": "food-001", "text": "상큼한 감귤 주스, 피로 회복에 좋습니다",
                "source": "injected", "injected_phrase": "피로 회복에 좋습니다"}
    monkeypatch.setattr(ct, "PRODUCTS_PATH", put("products.jsonl", [PRODUCT]))
    monkeypatch.setattr(ct, "SENTENCES_PATH", put("sentences.jsonl", [original]))
    monkeypatch.setattr(ct, "INJECTED_PATH", put("injected.jsonl", [injected]))
    monkeypatch.setattr(ct, "LABELS_PATH", put("labels.jsonl", [
        {"id": original["id"], "phrases": [{"phrase": "상큼한 감귤 주스", "type": "mood", "supported": True}]}]))
    answer = {"items": [{"n": 1, "phrases": [{"phrase": "상큼한 감귤 주스", "type": "mood", "supported": True},
                                             {"phrase": "피로 회복에 좋습니다", "type": "fact", "supported": False}]}]}
    monkeypatch.setattr(ct, "call_codex", lambda command, prompt, validator, stats, key_hint="": (validator(answer), None))

    ct.run_label(1)

    ids = {json.loads(line)["id"] for line in ct.LABELS_PATH.read_text("utf-8").splitlines()}
    assert ids == {original["id"], injected["id"]}


def test_label_run_also_labels_original_sentences_without_a_label(tmp_path, monkeypatch):
    from scripts.finetune import codex_teacher as ct

    def put(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
        return path

    labeled = {"id": "food-001-9b-t0-00", "product_id": "food-001", "text": "상큼한 감귤 주스", "source": "9b-t0"}
    unlabeled = {"id": "food-001-9b-t0-01", "product_id": "food-001", "text": "제주 감귤 100%", "source": "9b-t0"}
    monkeypatch.setattr(ct, "PRODUCTS_PATH", put("products.jsonl", [PRODUCT]))
    monkeypatch.setattr(ct, "SENTENCES_PATH", put("sentences.jsonl", [labeled, unlabeled]))
    monkeypatch.setattr(ct, "INJECTED_PATH", put("injected.jsonl", []))
    monkeypatch.setattr(ct, "LABELS_PATH", put("labels.jsonl", [
        {"id": labeled["id"], "phrases": [{"phrase": "상큼한 감귤 주스", "type": "mood", "supported": True}]}]))
    answer = {"items": [{"n": 1, "phrases": [{"phrase": "제주 감귤 100%", "type": "fact", "supported": True}]}]}
    monkeypatch.setattr(ct, "call_codex", lambda command, prompt, validator, stats, key_hint="": (validator(answer), None))

    ct.run_label(1)

    ids = {json.loads(line)["id"] for line in ct.LABELS_PATH.read_text("utf-8").splitlines()}
    assert ids == {labeled["id"], unlabeled["id"]}


def test_teacher_rules_quote_no_eval_or_regression_sentence():
    from pathlib import Path
    from scripts import eval_tools
    from scripts.finetune import codex_teacher as ct
    # quoting an eval sentence in the teacher prompt makes the eval measure the prompt, not the model (r2 report)
    texts = [text for text, _, _ in eval_tools.JUDGE_CASES]
    for name in ("eval.jsonl", "test.jsonl"):  # git-ignored, present where the labels are made
        path = Path(".local/finetune/judge") / name
        if path.exists():
            texts += [json.loads(line)["text"] for line in path.read_text("utf-8").splitlines() if line.strip()]
    assert [t for t in texts if t.strip() in ct.LABEL_RULES] == []


def test_label_keeps_the_good_items_of_a_chunk_with_one_bad_item(tmp_path, monkeypatch):
    from scripts.finetune import codex_teacher as ct

    def put(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
        return path

    good = {"id": "food-001-9b-t0-00", "product_id": "food-001", "text": "상큼한 감귤 주스", "source": "9b-t0"}
    bad = {"id": "food-001-9b-t0-01", "product_id": "food-001", "text": "제주 감귤 100%", "source": "9b-t0"}
    monkeypatch.setattr(ct, "PRODUCTS_PATH", put("products.jsonl", [PRODUCT]))
    monkeypatch.setattr(ct, "SENTENCES_PATH", put("sentences.jsonl", [good, bad]))
    monkeypatch.setattr(ct, "INJECTED_PATH", put("injected.jsonl", []))
    monkeypatch.setattr(ct, "LABELS_PATH", put("labels.jsonl", []))
    answer = {"items": [{"n": 1, "phrases": [{"phrase": "상큼한 감귤 주스", "type": "mood", "supported": True}]},
                        {"n": 2, "phrases": [{"phrase": "한라봉", "type": "fact", "supported": False}]}]}
    monkeypatch.setattr(ct, "call_codex", lambda command, prompt, validator, stats, key_hint="": (validator(answer), None))

    ct.run_label(1)

    ids = {json.loads(line)["id"] for line in ct.LABELS_PATH.read_text("utf-8").splitlines()}
    assert ids == {good["id"]}  # the bad one is left unlabeled for the next run


def test_held_out_splits_dev_and_test_products_per_category():
    products = {f"{c}-{i:03d}": {"category": c} for c in ("food", "living") for i in range(1, 16)}
    dev, test = jd.held_out(products)
    assert dev == {f"{c}-{i:03d}" for c in ("food", "living") for i in range(1, 7)}
    assert test == {f"{c}-{i:03d}" for c in ("food", "living") for i in range(7, 13)}
