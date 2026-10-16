import pytest

from pagegen import page as pg

CORPUS = "스테인리스 텀블러\n이중 진공 단열, 500ml, 색상 3종"


def ids(page):
    return [s["id"] for s in page["sections"]]


def test_numbers_compare_value_and_unit():
    assert pg.unsupported_numbers("500ml 넉넉하게", CORPUS) == []
    assert pg.unsupported_numbers("500 ML", CORPUS) == []
    assert pg.unsupported_numbers("3종 컬러", CORPUS) == []
    assert pg.unsupported_numbers("500g", CORPUS) == ["500g"]
    assert pg.unsupported_numbers("5가지 색", CORPUS) == ["5"]


def test_number_sourced_by_user_chat_only(page):
    chat = [{"role": "user", "text": "무게는 350g이에요"}, {"role": "assistant", "text": "420g 맞나요?"}]
    corpus = pg.corpus_of(page, chat)
    assert pg.unsupported_numbers("350g", corpus) == []
    assert pg.unsupported_numbers("420g", corpus) == ["420g"]


def test_split_line_tolerates_missing_space():
    assert pg.split_line("용량:500ml") == ("용량", "500ml")
    assert pg.split_line("운영 시간: 10:30") == ("운영 시간", "10:30")
    assert pg.split_line("설명만 있는 줄") == ("", "설명만 있는 줄")


def test_spec_values_must_appear_in_corpus():
    assert pg.unsupported_spec_values("용량: 500 ml\n색상:3종", CORPUS) == []
    assert pg.unsupported_spec_values("용량: 500ml\n소재: 티타늄", CORPUS) == ["티타늄"]


def test_update_text_rejects_unsourced_number_and_keeps_page(page):
    with pytest.raises(pg.ToolError, match="근거 없는 숫자 350g"):
        pg.update_text(page, "s1", "headline", "350g 초경량", CORPUS)
    assert page["sections"][0]["headline"] == "하루 종일 따뜻하게"


def test_update_text_calls_verify_after_number_check(page):
    seen = []
    assert pg.update_text(page, "s1", "headline", "온종일 따뜻", CORPUS, verify=seen.append) == "s1 headline 변경"
    assert seen == ["온종일 따뜻"] and page["sections"][0]["headline"] == "온종일 따뜻"


def test_update_text_verify_rejection_leaves_page(page):
    def deny(text):
        raise pg.ToolError("오류: 근거 없는 주장")

    with pytest.raises(pg.ToolError):
        pg.update_text(page, "s1", "body", "보냉도 됩니다", CORPUS, verify=deny)
    assert page["sections"][0]["body"] == "이중 진공 단열 텀블러"


def test_update_text_unknown_section_lists_ids(page):
    with pytest.raises(pg.ToolError, match="있는 섹션: s1, s2, s3"):
        pg.update_text(page, "s9", "headline", "x", CORPUS)


def test_update_text_bad_field(page):
    with pytest.raises(pg.ToolError, match="headline 또는 body"):
        pg.update_text(page, "s1", "caption", "x", CORPUS)


def test_spec_table_body_checks_values(page):
    pg.add_section(page, "spec_table", "s2", "제품 정보", "용량: 500ml", CORPUS)
    with pytest.raises(pg.ToolError, match="근거 없는 스펙 값 티타늄"):
        pg.update_text(page, "s4", "body", "소재: 티타늄", CORPUS)
    pg.update_text(page, "s4", "headline", "제품 정보 안내", CORPUS)  # headline is not a spec row


def test_add_section_ids_position_and_photo(page):
    assert pg.add_section(page, "scene", "s1", "책상 위", "", CORPUS) == "s4(scene) 추가"
    assert ids(page) == ["s1", "s4", "s2", "s3"]
    assert page["sections"][1]["photo"] == "p1"
    pg.add_section(page, "notice", "", "안내", "중성세제로 세척", CORPUS)
    assert ids(page) == ["s5", "s1", "s4", "s2", "s3"]


def test_add_section_rejects_unknown_kind_and_anchor(page):
    with pytest.raises(pg.ToolError, match="kind는"):
        pg.add_section(page, "carousel", "s1", "", "", CORPUS)
    with pytest.raises(pg.ToolError, match="없는 섹션 s9"):
        pg.add_section(page, "notice", "s9", "", "", CORPUS)
    assert ids(page) == ["s1", "s2", "s3"]


def test_remove_and_move(page):
    pg.move_section(page, "s3", "s1")
    assert ids(page) == ["s1", "s3", "s2"]
    pg.move_section(page, "s2", "")
    assert ids(page) == ["s2", "s1", "s3"]
    pg.remove_section(page, "s1")
    assert ids(page) == ["s2", "s3"]


def test_move_validates_before_moving(page):
    with pytest.raises(pg.ToolError):
        pg.move_section(page, "s3", "s9")
    with pytest.raises(pg.ToolError):
        pg.move_section(page, "s2", "s2")
    assert ids(page) == ["s1", "s2", "s3"]


def test_use_photo_and_edit_source(page):
    assert pg.edit_source(page, "s1")["id"] == "p1"  # edits start from the upload, not from p1-e1
    pg.use_photo(page, "s1", "p1")
    assert page["sections"][0]["photo"] == "p1"
    with pytest.raises(pg.ToolError, match="없는 사진 p9"):
        pg.use_photo(page, "s1", "p9")
    with pytest.raises(pg.ToolError, match="사진이 없습니다"):
        pg.use_photo(page, "s2", "p1")


def test_add_edit_numbers_per_source(page):
    photo = pg.add_edit(page, "p1", "주방")
    assert photo == {"id": "p1-e2", "file": "photos/p1-e2.png", "origin": "edit", "source": "p1", "instruction": "주방"}
    assert page["photos"][-1] is photo


def with_extra_upload(page):
    page["photos"].append({"id": "p2", "file": "photos/p2.jpg", "origin": "upload"})
    return page


def test_superlatives_need_a_source(page):
    assert pg.unsupported_superlatives("업계 최고 보온력", CORPUS) == ["최고"]
    assert pg.unsupported_superlatives("NO.1 텀블러", CORPUS) == ["no.1"]
    assert pg.unsupported_superlatives("최고의 선택", CORPUS + " 업계 최고 판매") == []
    with pytest.raises(pg.ToolError, match="근거 없는 최상급 표현 '최고'"):
        pg.update_text(page, "s1", "headline", "최고의 텀블러", CORPUS)


def test_set_variant(page):
    assert pg.set_variant(page, "s1", "overlay") == "s1 변형을 overlay로 변경"
    assert page["sections"][0]["variant"] == "overlay"
    with pytest.raises(pg.ToolError, match="hero 변형은 below, overlay 중 하나"):
        pg.set_variant(page, "s1", "grid-3")
    with pytest.raises(pg.ToolError, match="notice 섹션은 변형이 없습니다"):
        pg.set_variant(page, "s3", "dark")


def test_add_section_new_kinds_get_defaults(page):
    with_extra_upload(page)
    pg.add_section(page, "split", "s1", "한 손으로 여는 뚜껑", "누르면 열려요", CORPUS)
    pg.add_section(page, "gallery", "s2", "더 많은 모습", "", CORPUS)
    pg.add_section(page, "banner", "", "식은 커피, 아쉽지 않으셨나요?", "", CORPUS)
    split, gallery, banner = (next(s for s in page["sections"] if s["kind"] == k) for k in ("split", "gallery", "banner"))
    assert (split["photo"], split["variant"]) == ("p2", "image-left")
    assert (gallery["photos"], gallery["variant"]) == (["p2"], "grid-2") and "photo" not in gallery
    assert banner["variant"] == "light" and "photo" not in banner


def test_gallery_needs_extra_uploads(page):
    with pytest.raises(pg.ToolError, match="갤러리에 넣을 추가 사진이 없습니다"):
        pg.add_section(page, "gallery", "s1", "더 많은 모습", "", CORPUS)


def test_photo_tools_reject_gallery(page):
    with_extra_upload(page)
    pg.add_section(page, "gallery", "s1", "더 많은 모습", "", CORPUS)
    with pytest.raises(pg.ToolError, match="하나씩 바꿀 수 없습니다"):
        pg.use_photo(page, "s4", "p1")
    with pytest.raises(pg.ToolError, match="하나씩 바꿀 수 없습니다"):
        pg.edit_source(page, "s4")
