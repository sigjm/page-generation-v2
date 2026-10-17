import json

import pytest

from pagegen import agent
from pagegen.llm import LLMError

OK = {"phrases": []}


def call(name, **args):
    return {"id": f"c-{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def flag(phrase):
    return {"phrases": [{"phrase": phrase, "type": "fact", "supported": False}]}


class FakeLLM:
    """Scripted stand-in for pagegen.llm: pops one reply per call, raises replies that are exceptions."""
    JUDGE_MODEL = "judge-model"
    COPY_MODEL = "copy-model"

    def __init__(self, chats=(), jsons=(), edit=b"\x89PNG fake"):
        self.chats, self.jsons, self.edit = list(chats), list(jsons), edit
        self.chat_calls, self.json_calls, self.json_kw = [], [], []

    def chat(self, messages, **kw):
        self.chat_calls.append(json.loads(json.dumps(messages)))
        return self.chats.pop(0)

    def chat_json(self, messages, schema, **kw):
        self.json_calls.append(messages)
        self.json_kw.append(kw)
        reply = self.jsons.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def edit_image(self, file, instruction):
        if isinstance(self.edit, Exception):
            raise self.edit
        return self.edit

    def model_jpeg(self, file):
        return b"jpeg"

    def image_part(self, jpeg):
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,"}}


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "photos").mkdir()
    return tmp_path


def test_update_text_then_reply(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="headline", text="온종일 따뜻")]},
                          {"content": "헤드라인을 줄였어요."}], jsons=[OK])
    chat = []
    reply, changed = agent.chat_turn(folder, page, chat, "헤드라인 짧게", fake)
    assert changed and page["sections"][0]["headline"] == "온종일 따뜻"
    assert reply == {"role": "assistant", "text": "헤드라인을 줄였어요.", "tools": [
        {"name": "update_text", "args": {"section_id": "s1", "field": "headline", "text": "온종일 따뜻"},
         "ok": True, "result": "s1 headline 변경"}]}
    assert [m["role"] for m in chat] == ["user", "assistant"]
    assert fake.chat_calls[0][0]["role"] == "system" and '"s1"' in fake.chat_calls[0][0]["content"]


def test_tool_error_goes_back_to_model(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("remove_section", section_id="s9")]}, {"content": "없는 섹션이었어요."}])
    _, changed = agent.chat_turn(folder, page, [], "지워", fake)
    assert not changed
    tool_message = fake.chat_calls[1][-1]
    assert tool_message["role"] == "tool" and "없는 섹션 s9" in tool_message["content"]


def test_ask_user_ends_turn(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("ask_user", question="무게는 얼마인가요?"),
                                          call("remove_section", section_id="s3")]}])
    reply, changed = agent.chat_turn(folder, page, [], "무게도 적어줘", fake)
    assert reply["text"] == "무게는 얼마인가요?" and len(fake.chat_calls) == 1
    assert not changed and len(page["sections"]) == 3  # calls after ask_user do not run


def test_round_cap(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("remove_section", section_id="s9")]}] * 4)
    reply, _ = agent.chat_turn(folder, page, [], "x", fake)
    assert reply["text"] == "여기까지 반영했어요." and len(fake.chat_calls) == 4


def test_unsourced_number_rejected_without_judge(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="body", text="350g 초경량")]},
                          {"content": "확인이 필요해요."}])
    _, changed = agent.chat_turn(folder, page, [], "가볍다고 써줘", fake)
    assert not changed and fake.json_calls == []


def test_number_said_by_user_is_allowed(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="body", text="350g 가벼운 무게")]},
                          {"content": "반영했어요."}], jsons=[OK])
    _, changed = agent.chat_turn(folder, page, [], "무게 350g이라고 써줘", fake)
    assert changed


def test_judge_flag_rejects(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="headline", text="따뜻하고 차갑게")]},
                          {"content": "뺐어요."}], jsons=[flag("차갑게")])
    reply, changed = agent.chat_turn(folder, page, [], "x", fake)
    assert not changed and "근거 없는 주장 '차갑게'" in reply["tools"][0]["result"]


def test_judge_failure_is_fail_closed(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="headline", text="따뜻하게")]},
                          {"content": "실패했어요."}], jsons=[LLMError("down")])
    reply, changed = agent.chat_turn(folder, page, [], "x", fake)
    assert not changed and "주장 검사를 하지 못해" in reply["tools"][0]["result"]


def test_judge_sees_user_message_and_photo_basis(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s2", field="body", text="식기세척기 사용 가능")]},
                          {"content": "넣었어요."}], jsons=[OK])
    agent.chat_turn(folder, page, [], "식기세척기 사용 가능하다고 써줘", fake)
    basis = fake.json_calls[0][1]["content"]
    assert "식기세척기 사용 가능하다고 써줘" in basis and "원터치 뚜껑" in basis


def test_malformed_arguments_are_a_tool_error(page, folder):
    bad = {"id": "c1", "type": "function", "function": {"name": "update_text", "arguments": "{section_id: s1"}}
    fake = FakeLLM(chats=[{"tool_calls": [bad]}, {"content": "다시 할게요."}])
    reply, changed = agent.chat_turn(folder, page, [], "x", fake)
    assert not changed and "인자 JSON" in reply["tools"][0]["result"]


def test_missing_argument(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="body")]}, {"content": "."}])
    reply, _ = agent.chat_turn(folder, page, [], "x", fake)
    assert "인자 text가 빠졌습니다" in reply["tools"][0]["result"]


def test_unknown_tool(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("delete_everything")]}, {"content": "."}])
    reply, _ = agent.chat_turn(folder, page, [], "x", fake)
    assert "없는 도구 delete_everything" in reply["tools"][0]["result"]


def test_edit_photo_writes_new_file_from_upload(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("edit_photo", section_id="s1", instruction="밝은 주방")]},
                          {"content": "바꿨어요."}])
    _, changed = agent.chat_turn(folder, page, [], "배경 주방으로", fake)
    assert changed and page["sections"][0]["photo"] == "p1-e2"
    assert (folder / "photos/p1-e2.png").read_bytes() == fake.edit


def test_edit_photo_failure_keeps_photo(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("edit_photo", section_id="s1", instruction="주방")]},
                          {"content": "실패했어요."}], edit=LLMError("timeout"))
    reply, changed = agent.chat_turn(folder, page, [], "배경 주방으로", fake)
    assert not changed and page["sections"][0]["photo"] == "p1-e1"
    assert "사진 편집에 실패" in reply["tools"][0]["result"]


def test_text_model_error_propagates(page, folder):
    class Down(FakeLLM):
        def chat(self, messages, **kw):
            raise LLMError("모델 서버 오류: 연결 거부")

    with pytest.raises(LLMError):
        agent.chat_turn(folder, page, [], "x", Down())


def test_history_is_last_six_turns_text_only(page, folder):
    chat = [{"role": r, "text": f"{r}{i}", "tools": []} for i in range(10) for r in ("user", "assistant")]
    fake = FakeLLM(chats=[{"content": "네"}])
    reply, changed = agent.chat_turn(folder, page, chat, "고마워", fake)
    sent = fake.chat_calls[0]
    assert len(sent) == 1 + 12 + 1
    assert sent[1] == {"role": "user", "content": "user4"} and sent[-1] == {"role": "user", "content": "고마워"}
    assert reply["text"] == "네" and not changed


ANALYSIS = {"category": "주방용품", "visible_features": ["원터치 뚜껑"], "colors": ["실버"],
            "facts": [{"key": "용량", "value": "500ml"}, {"key": "무게", "value": "350g"}],
            "questions": ["무게는 얼마인가요?"]}
COPY = {"hero": {"headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러"},
        "points": {"headline": "이래서 좋아요", "items": [
            {"title": "넉넉한 용량", "text": "500ml"}, {"title": "가벼움", "text": "350g 초경량"},
            {"title": "보냉", "text": "차갑게도 유지"}]},
        "scene": {"headline": "어디서나", "body": "", "photo_instruction": "원목 식탁 위"},
        "notice": {"headline": "관리", "body": "중성세제로 씻어 주세요. 습기에 주의하세요."}}
PRODUCT = {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종. 중성세제로 씻어 주세요"}


class JudgeByContent(FakeLLM):
    """Analysis and copy are scripted; every judge call flags the text when it contains a `bad` phrase."""

    def __init__(self, scripted, bad=(), fail=False, edit=b"png", unjudgeable=()):
        super().__init__(jsons=scripted, edit=edit)
        self.bad, self.fail, self.unjudgeable, self.judged = bad, fail, unjudgeable, []

    def chat_json(self, messages, schema, **kw):
        if self.jsons:
            return super().chat_json(messages, schema, **kw)
        if self.fail:
            raise LLMError("down")
        text = messages[1]["content"].split("새 문구: ", 1)[1]
        if any(u in text for u in self.unjudgeable):  # e.g. the 9B looping until max_tokens, twice
            raise LLMError("모델 응답을 두 번 받지 못했습니다: Unterminated string")
        self.judged.append(text)
        return flag(text) if any(b in text for b in self.bad) else OK


def draft(folder, bad=(), fail=False, edit=b"png", scripted=None, unjudgeable=()):
    fake = JudgeByContent([ANALYSIS, COPY] if scripted is None else scripted, bad=bad, fail=fail, edit=edit,
                          unjudgeable=unjudgeable)
    photos = [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"}]
    page, message = agent.make_draft(folder, "20261016-153012-ab12", dict(PRODUCT), photos, fake)
    return page, message, fake


def test_draft_assembles_cleans_and_edits_scene(folder):
    page, message, fake = draft(folder, bad=["차갑게"])
    assert [(s["id"], s["kind"]) for s in page["sections"]] == [
        ("s1", "hero"), ("s2", "points"), ("s3", "scene"), ("s4", "spec_table"), ("s5", "notice")]
    assert page["sections"][1]["body"] == "넉넉한 용량: 500ml"  # 350g had no source, 보냉 was flagged
    assert page["sections"][3]["body"] == "용량: 500ml"  # the 350g fact was not in the notes
    assert page["analysis"]["facts"] == [{"key": "용량", "value": "500ml"}]
    assert page["sections"][0]["photo"] == "p1"
    assert page["sections"][2]["photo"] == "p1-e1" and (folder / "photos/p1-e1.png").exists()
    assert page["version"] == 1 and page["product"] == PRODUCT
    assert "무게는 얼마인가요?" in message["text"] and message["role"] == "assistant"
    assert fake.json_calls[0][1]["content"][0]["type"] == "image_url"
    assert "가벼움: 350g 초경량" not in fake.judged  # number check runs first, no judge call wasted


def test_draft_judges_sentence_by_sentence(folder):
    page, _, fake = draft(folder, bad=["습기에 주의"])
    assert page["sections"][-1]["body"] == "중성세제로 씻어 주세요."  # only the unsourced sentence goes
    assert "중성세제로 씻어 주세요." in fake.judged and "습기에 주의하세요." in fake.judged


def test_draft_flagged_headline_becomes_product_name(folder):
    page, _, _ = draft(folder, bad=["하루 종일 따뜻하게"])
    assert page["sections"][0]["headline"] == "스테인리스 텀블러"
    assert page["sections"][0]["body"] == "이중 진공 단열 텀블러"


def test_draft_point_with_any_flagged_part_is_dropped(folder):
    copy = {**COPY, "points": {"headline": "이래서 좋아요", "items": [
        {"title": "넉넉한 용량", "text": "500ml. 손으로 하나하나 만들었어요."}]}}
    page, _, _ = draft(folder, bad=["손으로"], scripted=[ANALYSIS, copy])
    assert all(s["kind"] != "points" for s in page["sections"])  # no half-points without their title


def test_draft_judge_failure_means_no_draft(folder):
    with pytest.raises(LLMError):
        draft(folder, fail=True)


def test_draft_scene_edit_failure_keeps_original(folder):
    page, _, _ = draft(folder, edit=LLMError("timeout"))
    scene = next(s for s in page["sections"] if s["kind"] == "scene")
    assert scene["photo"] == "p1" and len(page["photos"]) == 1


def test_draft_broken_analysis_raises(folder):
    with pytest.raises(LLMError):
        draft(folder, scripted=[LLMError("JSON 두 번 실패")])


def test_chat_verify_judges_each_sentence(page, folder):
    text = "온종일 든든한 텀블러. 차가운 음료도 시원하게."
    fake = JudgeByContent([], bad=["차가운"])
    fake.chats = [{"tool_calls": [call("update_text", section_id="s1", field="body", text=text)]}, {"content": "뺐어요."}]
    reply, changed = agent.chat_turn(folder, page, [], "본문 바꿔줘", fake)
    assert not changed and "차가운 음료도 시원하게." in reply["tools"][0]["result"]
    assert fake.judged == ["온종일 든든한 텀블러.", "차가운 음료도 시원하게."]


def test_normalize_analysis_tolerates_junk():
    raw = {"category": None, "visible_features": ["a", "", "b"], "colors": "red",
           "facts": [{"key": "용량", "value": "500ml"}, "x", {"key": "", "value": "1"}],
           "questions": ["q1", "q2", "q3", "q4"]}
    assert agent.normalize_analysis(raw, "500ml") == {
        "category": "", "visible_features": ["a", "b"], "colors": [],
        "facts": [{"key": "용량", "value": "500ml"}], "questions": ["q1", "q2", "q3"]}


def test_update_judges_only_new_sentences(page, folder):
    page["sections"][1]["body"] = "넉넉한 용량: 500ml\n원터치: 한 손으로 열어요"
    fake = JudgeByContent([], bad=["한 손으로"])  # an existing line the judge would now flag
    new_body = "넉넉한 용량: 500ml\n원터치: 한 손으로 열어요\n가벼움: 들고 다니기 편해요"
    fake.chats = [{"tool_calls": [call("update_text", section_id="s2", field="body", text=new_body)]},
                  {"content": "추가했어요."}]
    _, changed = agent.chat_turn(folder, page, [], "가벼움도 넣어줘", fake)
    assert changed and page["sections"][1]["body"] == new_body
    assert fake.judged == ["가벼움: 들고 다니기 편해요"]


def test_normalize_analysis_checks_numbers_in_the_whole_row():
    raw = {"facts": [{"key": "12개월 품질보증", "value": "가능"}, {"key": "용량", "value": "500ml"}]}
    assert agent.normalize_analysis(raw, "500ml, 반품 가능")["facts"] == [{"key": "용량", "value": "500ml"}]


def test_draft_drops_a_sentence_the_judge_cannot_answer(folder):
    page, _, _ = draft(folder, unjudgeable=["습기에 주의"])
    assert page["sections"][-1]["body"] == "중성세제로 씻어 주세요."  # unjudged text never reaches the page


def test_draft_unjudgeable_headline_becomes_product_name(folder):
    page, _, _ = draft(folder, unjudgeable=["하루 종일 따뜻하게"])
    assert page["sections"][0]["headline"] == "스테인리스 텀블러"


def test_set_variant_tool_changes_layout(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("set_variant", section_id="s1", variant="overlay")]},
                          {"content": "글씨를 사진 위로 올렸어요."}])
    reply, changed = agent.chat_turn(folder, page, [], "히어로 글씨를 사진 위에 올려줘", fake)
    assert changed and page["sections"][0]["variant"] == "overlay"
    assert reply["tools"][0]["result"] == "s1 변형을 overlay로 변경"


def test_tools_and_rules_know_the_new_kinds():
    from pagegen import prompts
    names = [t["function"]["name"] for t in prompts.TOOLS]
    assert len(names) == 8 and "set_variant" in names
    for word in ("banner", "split", "gallery", "set_variant", "overlay", "grid-3"):
        assert word in prompts.CHAT_RULES


CAPTIONS = [{"caption": "뚜껑을 연 모습"}, {"caption": "무광 표면 클로즈업"}]
COPY_L = {**COPY, "banner": {"headline": "식은 커피, 아쉽지 않으셨나요?"},
          "details": [{"headline": "한 손으로 여는 뚜껑", "body": "원터치로 열려요."},
                      {"headline": "매끈한 무광 표면", "body": "손자국이 덜 보여요."}]}


def draft_n(folder, n, bad=(), copy=COPY_L, captions=CAPTIONS):
    # call order: analysis, one caption call per split photo (p2, p3), copy, then judge calls
    fake = JudgeByContent([ANALYSIS, *captions[:max(0, min(2, n - 1))], copy], bad=bad)
    photos = [{"id": f"p{i}", "file": f"photos/p{i}.jpg", "origin": "upload"} for i in range(1, n + 1)]
    page, _ = agent.make_draft(folder, "20261016-153012-ab12", dict(PRODUCT), photos, fake)
    return page, fake


def layout(page):
    return [(s["kind"], s.get("variant"), s.get("photo") or s.get("photos")) for s in page["sections"]]


def test_draft_places_every_photo(folder):
    page, fake = draft_n(folder, 5)
    assert layout(page) == [
        ("hero", "below", "p1"), ("banner", "light", None), ("points", "cards", None),
        ("split", "image-left", "p2"), ("split", "image-right", "p3"), ("scene", None, "p1-e1"),
        ("gallery", "grid-2", ["p4", "p5"]), ("spec_table", None, None), ("notice", None, None)]
    assert [s["id"] for s in page["sections"]] == [f"s{i}" for i in range(1, 10)]
    for call_messages in fake.json_calls[1:3]:  # each split photo is described on its own
        assert [part["type"] for part in call_messages[1]["content"]] == ["image_url", "text"]
    copy_input = fake.json_calls[3][1]["content"]
    assert "사진 2: 뚜껑을 연 모습" in copy_input and "사진 3: 무광 표면 클로즈업" in copy_input
    assert page["analysis"]["photo_captions"] == ["", "뚜껑을 연 모습", "무광 표면 클로즈업"]


def test_draft_one_photo_keeps_the_old_layout(folder):
    page, _ = draft_n(folder, 1, copy={**COPY_L, "details": []})
    assert [s["kind"] for s in page["sections"]] == ["hero", "banner", "points", "scene", "spec_table", "notice"]


def test_draft_split_without_text_sends_its_photo_to_gallery(folder):
    page, _ = draft_n(folder, 3, copy={**COPY_L, "details": COPY_L["details"][:1]})  # no copy for p3
    assert layout(page)[3:6] == [("split", "image-left", "p2"), ("scene", None, "p1-e1"), ("gallery", "stack", ["p3"])]


def test_draft_flagged_banner_and_split_headline_become_empty(folder):
    page, _ = draft_n(folder, 2, bad=["식은 커피", "한 손으로"])
    kinds = [s["kind"] for s in page["sections"]]
    assert "banner" not in kinds  # an empty banner says nothing
    split = next(s for s in page["sections"] if s["kind"] == "split")
    assert split["headline"] == "" and split["body"] == "원터치로 열려요."


def test_draft_drops_unsourced_superlatives_without_judging(folder):
    copy = {**COPY_L, "hero": {"headline": "하루 종일 따뜻하게", "body": "업계 최고의 보온력. 이중 진공 단열 텀블러."}}
    page, fake = draft_n(folder, 1, copy={**copy, "details": []})
    assert page["sections"][0]["body"] == "이중 진공 단열 텀블러."
    assert "업계 최고의 보온력." not in fake.judged


def test_basis_includes_photo_captions(page):
    page["analysis"]["photo_captions"] = ["뚜껑을 연 모습"]
    assert "뚜껑을 연 모습" in agent.basis_of(page, [])


def test_draft_banner_repeating_the_hero_is_dropped(folder):
    copy = {**COPY_L, "banner": {"headline": COPY_L["hero"]["headline"]}, "details": []}
    page, _ = draft_n(folder, 1, copy=copy)
    assert "banner" not in [s["kind"] for s in page["sections"]]


def test_draft_failed_caption_sends_the_photo_to_gallery(folder):
    page, _ = draft_n(folder, 2, captions=[LLMError("JSON 두 번 실패")], copy={**COPY_L, "details": []})
    assert layout(page)[3:5] == [("scene", None, "p1-e1"), ("gallery", "stack", ["p2"])]


def test_draft_hanja_in_headline_falls_back_to_product_name(folder):
    copy = {**COPY_L, "hero": {"headline": "고雅的한 텀블러", "body": "이중 진공 단열 텀블러."}, "details": []}
    page, fake = draft_n(folder, 1, copy=copy)
    assert page["sections"][0]["headline"] == "스테인리스 텀블러"
    assert "고雅的한 텀블러" not in fake.judged  # rejected mechanically, no judge call


def test_draft_failed_caption_does_not_shift_copy_to_the_wrong_photo(folder):
    copy = {**COPY_L, "details": [{"headline": "매끈한 무광 표면", "body": "손자국이 덜 보여요."}]}
    page, _ = draft_n(folder, 3, captions=[LLMError("JSON 두 번 실패"), {"caption": "무광 표면 클로즈업"}], copy=copy)
    split = next(s for s in page["sections"] if s["kind"] == "split")
    assert (split["photo"], split["headline"]) == ("p3", "매끈한 무광 표면")
    assert next(s for s in page["sections"] if s["kind"] == "gallery")["photos"] == ["p2"]


def test_normalize_analysis_rejects_unsourced_hanja_and_superlative_keys():
    raw = {"facts": [{"key": "材質", "value": "500ml"}, {"key": "최고 용량", "value": "500ml"},
                     {"key": "용량", "value": "500ml"}]}
    assert agent.normalize_analysis(raw, "500ml")["facts"] == [{"key": "용량", "value": "500ml"}]


def test_judge_calls_use_the_judge_model(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="headline", text="온종일 따뜻")]},
                          {"content": "줄였어요."}], jsons=[OK])
    agent.chat_turn(folder, page, [], "헤드라인 짧게", fake)
    assert fake.json_kw[0]["model"] == "judge-model"


def test_judge_messages_match_the_runtime_prompt():
    from pagegen import prompts
    assert agent.judge_messages("문장", "근거") == [{"role": "system", "content": prompts.JUDGE_SYSTEM},
                                                  {"role": "user", "content": "근거\n새 문구: 문장"}]


def test_copy_messages_match_the_runtime_prompt():
    from pagegen import prompts
    analysis = {"category": "식품", "facts": []}
    assert agent.copy_messages("상품명: 감귤", analysis, "사진 설명 없음: details는 빈 목록.") == [
        {"role": "system", "content": prompts.COPY_SYSTEM},
        {"role": "user", "content": '상품명: 감귤\n사진 분석: {"category": "식품", "facts": []}\n사진 설명 없음: details는 빈 목록.'}]


def test_draft_copy_uses_the_copy_model(folder):
    _, _, fake = draft(folder)
    assert "model" not in fake.json_kw[0] and fake.json_kw[1]["model"] == "copy-model"  # analysis, then copy


def test_check_sections_reports_dropped_sentences_with_reasons():
    sections = [{"kind": "hero", "headline": "최고의 텀블러", "body": "이중 진공 단열. 12시간 보냉."},
                {"kind": "notice", "headline": "안내", "body": "습기에 주의하세요."}]
    fake = JudgeByContent([], bad=["습기"])
    kept, tally = agent.check_sections(sections, "스테인리스 텀블러\n이중 진공 단열", "근거", "스테인리스 텀블러", fake)
    assert kept == [{"kind": "hero", "headline": "스테인리스 텀블러", "body": "이중 진공 단열."}]  # empty notice settled away
    assert tally["dropped"] == [{"text": "최고의 텀블러", "reason": "superlative"},
                                {"text": "12시간 보냉.", "reason": "number"},
                                {"text": "습기에 주의하세요.", "reason": "judge"}]


def test_scene_copy_repeating_the_photo_instruction_is_dropped():
    copy = {**COPY, "scene": {"headline": "바닷가 캠핑장의 아침", "photo_instruction": "바닷가 캠핑장의 아침",
                              "body": "바닷가 캠핑장의 아침. 따뜻한 커피 한 잔과 함께."}}
    sections, instruction = agent.build_sections(copy, ANALYSIS, "스테인리스 텀블러",
                                                 [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"}])
    scene = next(s for s in sections if s["kind"] == "scene")
    assert (scene["headline"], scene["body"], instruction) == ("", "따뜻한 커피 한 잔과 함께.", "바닷가 캠핑장의 아침")


def test_copy_prompt_has_category_tones_and_no_scene_example():
    from pagegen import prompts
    assert "아침 햇살" not in prompts.COPY_SYSTEM and "한글로만" in prompts.COPY_SYSTEM
    assert all(f"{c}:" in prompts.COPY_SYSTEM for c in ("식품", "화장품", "가전·전자", "의류·잡화", "생활·주방"))
