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


def test_langsmith_logging_needs_a_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert ej.log_to_langsmith([], [], "m") is None


def test_dataset_name_changes_with_the_labels():
    a = [{"id": "x", "flagged": True}]
    b = [{"id": "x", "flagged": False}]
    assert ej.dataset_name(a) != ej.dataset_name(b) and ej.dataset_name(a).startswith("pagegen-judge-eval-")
