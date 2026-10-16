# 섹션 레이아웃 확장 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 섹션 8종·변형·사진 자동 배치·리서치 기반 문구 규칙·최상급 표현 검사를 더해, 올린 사진이 모두 페이지에 들어가고 채팅으로 배치를 바꿀 수 있게 한다.

**Architecture:** `page.py`에 종류·변형 표와 `set_variant`·최상급 검사를 두고, `agent.py`의 초안 조립이 사진을 hero·split·gallery에 나눠 넣는다. 렌더러는 `data-variant`로 CSS를 고른다. 모델 호출 수는 늘지 않는다(분석이 사진 설명을, 문구가 배너·split 문구를 같은 호출에서 함께 낸다).

**Tech Stack:** 기존과 같음 (Python 3.13+, FastAPI, Pillow, Playwright, pytest).

**Spec:** `docs/superpowers/specs/2026-10-16-section-layouts-design.md` (기반: `docs/superpowers/specs/2026-10-16-conversational-detail-page-agent-design.md`)

## Global Constraints

- 새 의존성 없음. 외부 리소스 없음. 모델 문자열은 `html.escape`.
- 기존 page.json(변형 없음, 5종)도 그대로 읽히고 그려져야 한다.
- `KINDS = ("hero", "banner", "points", "split", "scene", "gallery", "spec_table", "notice")`, `PHOTO_KINDS = ("hero", "scene", "split")`.
- `VARIANTS = {"hero": ("below", "overlay"), "banner": ("light", "dark"), "points": ("cards", "numbered"), "split": ("image-left", "image-right"), "gallery": ("grid-2", "grid-3", "stack")}` — 첫 값이 기본.
- `SUPERLATIVES = ("최고", "최상", "최초", "1위", "완벽", "유일", "최저가", "넘버원", "no.1")`, 대소문자 무시.
- 갤러리 고정 제목 `"더 많은 모습"`(`page.GALLERY_TITLE`), 검사 대상 아님.
- 사용자에게 보이는 문구는 한국어. Codex는 git 상태를 바꾸지 않는다.

## Review Focus

1. **사진 1장만 올린 경우** → split·gallery 없이 기존 구성(+배너)이어야 한다. (Task 3: `test_draft_one_photo_keeps_the_old_layout`)
2. **문구 모델이 `details`를 split 사진 수보다 적게 주거나 안 주는 경우** → 문구 없는 split은 빠지고 그 사진은 갤러리로 간다(사진 유실 없음). (Task 3: `test_draft_split_without_text_sends_its_photo_to_gallery`)
3. **page.json의 `variant`가 그 종류에 없는 값이거나 없는 경우**(손으로 고친 파일·옛 파일) → 기본 변형으로 그린다. (Task 4: `test_variant_attribute_and_default`)
4. **갤러리 `photos`에 없는 사진 id가 섞인 경우** → 그 사진만 건너뛴다. (Task 4: `test_gallery_renders_known_photos_only`)
5. **"No.1"처럼 대소문자가 섞인 최상급 표현** → 막혀야 한다. (Task 1: `test_superlatives_need_a_source`)

## 순서와 분담

```text
Claude: 1 → 2 → 3 ─────────→ (검수·커밋)
Codex:      └→ 4 (1 끝나면)    └→ 5 (2·3 뒤) → 6 (4·5 뒤)
```

---

### Task 1: 종류·변형·최상급 검사 (`page.py`, Claude)

**Files:** Modify `pagegen/page.py`, `tests/test_page.py`

**Interfaces — Produces (`pagegen.page`):** `KINDS`, `PHOTO_KINDS`, `VARIANTS`, `SUPERLATIVES`, `GALLERY_TITLE`, `unsupported_superlatives(text, corpus) -> list[str]`, `set_variant(page, section_id, variant) -> str`. `check_text`가 최상급 검사를 포함한다. `add_section`이 변형 기본값·split 사진·gallery 사진을 채운다. 사진 도구는 gallery에 대해 전용 오류를 낸다.

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_page.py` 끝)

```python
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
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_page.py -q` / Expected: 5 failed (`AttributeError: ... unsupported_superlatives` 등)

- [ ] **Step 3: `pagegen/page.py` 수정**

상수 교체(파일 위쪽):

```python
KINDS = ("hero", "banner", "points", "split", "scene", "gallery", "spec_table", "notice")
PHOTO_KINDS = ("hero", "scene", "split")
VARIANTS = {"hero": ("below", "overlay"), "banner": ("light", "dark"), "points": ("cards", "numbered"),
            "split": ("image-left", "image-right"), "gallery": ("grid-2", "grid-3", "stack")}
SUPERLATIVES = ("최고", "최상", "최초", "1위", "완벽", "유일", "최저가", "넘버원", "no.1")
GALLERY_TITLE = "더 많은 모습"
```

`unsupported_spec_values` 아래에 추가하고 `check_text`에 끼운다:

```python
def unsupported_superlatives(text: str, corpus: str) -> list[str]:
    # ponytail: fixed word list; roundabout wording ("가장 좋은") is left to the claim judge
    low, have = text.casefold(), corpus.casefold()
    return [word for word in SUPERLATIVES if word in low and word not in have]


def check_text(text: str, corpus: str, *, spec: bool = False) -> None:
    bad = unsupported_numbers(text, corpus)
    if bad:
        raise ToolError(f"오류: 근거 없는 숫자 {', '.join(bad)}. 사용자에게 ask_user로 물어보세요.")
    bad = unsupported_superlatives(text, corpus)
    if bad:
        raise ToolError(f"오류: 근거 없는 최상급 표현 '{', '.join(bad)}'. 빼고 다시 쓰세요.")
    bad = unsupported_spec_values(text, corpus) if spec else []
    if bad:
        raise ToolError(f"오류: 근거 없는 스펙 값 {', '.join(bad)}. 사용자에게 ask_user로 물어보세요.")
```

`add_section` 교체:

```python
def add_section(page: dict, kind: str, after_section_id: str, headline: str, body: str, corpus: str,
                verify: Callable[[str], None] = _no_check) -> str:
    if kind not in KINDS:
        raise ToolError(f"오류: kind는 {', '.join(KINDS)} 중 하나입니다.")
    position = _index(page, after_section_id) + 1 if after_section_id else 0
    check_text(headline, corpus)
    check_text(body, corpus, spec=kind == "spec_table")
    section = {"id": next_section_id(page), "kind": kind, "headline": headline, "body": body}
    if kind in VARIANTS:
        section["variant"] = VARIANTS[kind][0]
    uploads = [p["id"] for p in page["photos"] if p["origin"] == "upload"]
    if kind == "gallery":
        section["photos"] = uploads[1:]
        if not section["photos"]:
            raise ToolError("오류: 갤러리에 넣을 추가 사진이 없습니다.")
    elif kind in PHOTO_KINDS:
        section["photo"] = uploads[1] if kind == "split" and len(uploads) > 1 else uploads[0]
    verify(f"{headline}\n{body}")
    page["sections"].insert(position, section)
    return f"{section['id']}({kind}) 추가"


def set_variant(page: dict, section_id: str, variant: str) -> str:
    section = find_section(page, section_id)
    allowed = VARIANTS.get(section["kind"])
    if not allowed:
        raise ToolError(f"오류: {section['kind']} 섹션은 변형이 없습니다.")
    if variant not in allowed:
        raise ToolError(f"오류: {section['kind']} 변형은 {', '.join(allowed)} 중 하나입니다.")
    section["variant"] = variant
    return f"{section_id} 변형을 {variant}로 변경"
```

`_photo_section` 교체:

```python
def _photo_section(page: dict, section_id: str) -> dict:
    section = find_section(page, section_id)
    if section["kind"] == "gallery":
        raise ToolError("오류: gallery 섹션은 사진을 하나씩 바꿀 수 없습니다. remove_section 후 add_section(gallery)를 쓰세요.")
    if section["kind"] not in PHOTO_KINDS:
        raise ToolError(f"오류: {section['kind']} 섹션에는 사진이 없습니다. 사진은 hero·scene·split에만 있습니다.")
    return section
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q` / Expected: 전체 통과 (`test_page.py` 21개)
- [ ] **Step 5: 커밋** — `git add pagegen/page.py tests/test_page.py && git commit -m "feat: section kinds/variants, set_variant and superlative check"`
- [ ] **Step 6: Codex에게 Task 4 브리프 발행**

---

### Task 2: 채팅 도구 `set_variant` (Claude)

**Files:** Modify `pagegen/prompts.py`, `pagegen/agent.py`, `tests/test_agent.py`

**Interfaces — Consumes:** `page.set_variant`. **Produces:** `prompts.TOOLS` 8개(`set_variant` 추가), `CHAT_RULES` 섹션 설명 갱신, `agent._run_tool`의 `set_variant` 경로, `MUTATING`에 포함.

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_agent.py` 끝)

```python
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
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_agent.py -q` / Expected: 2 failed

- [ ] **Step 3: 구현**

`pagegen/prompts.py` — `CHAT_RULES`의 6번 규칙 뒤에 7번을 넣고, "섹션 종류:" 줄을 바꾼다:

```text
7. 글씨를 사진 위에 올리기, 사진을 왼쪽·오른쪽으로, 번호 붙이기, 2열·3열·한 장씩 크게처럼 배치나 모양을 바꾸는 요청은 set_variant를 쓴다.
섹션 종류(변형): hero(대표 사진+헤드라인; below|overlay=글씨를 사진 위에), banner(큰 한 문장; light|dark), points(특징, 본문 한 줄에 '제목: 설명'; cards|numbered), split(사진+설명 좌우; image-left|image-right), scene(연출 사진+설명), gallery(사진 여러 장; grid-2|grid-3|stack), spec_table(제품 정보, 한 줄에 '항목: 값'), notice(관리·주의).
```

`TOOLS`에 추가(`move_section` 다음):

```python
    _tool("set_variant", "섹션의 배치·모양을 바꾼다. hero: below|overlay(글씨를 사진 위에), banner: light|dark, "
          "points: cards|numbered(번호), split: image-left|image-right(사진 위치), gallery: grid-2|grid-3|stack(한 장씩 크게).",
          {"section_id": _S, "variant": _S}),
```

`pagegen/agent.py` — `MUTATING`에 `"set_variant"`를 넣고, `_run_tool`의 `match`에 추가:

```python
            case "set_variant":
                return pg.set_variant(page, a["section_id"], a["variant"])
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q` / Expected: 전체 통과
- [ ] **Step 5: 커밋** — `git commit -am "feat: set_variant chat tool and section/variant rules"` (해당 3파일만 add)

---

### Task 3: 사진 자동 배치와 리서치 문구 규칙 (초안, Claude)

**Files:** Modify `pagegen/prompts.py`, `pagegen/agent.py`, `tests/test_agent.py`

**Interfaces — Produces:** `ANALYZE_SCHEMA`에 `photo_captions`, `COPY_SCHEMA`에 `banner{headline}`·`details[{headline, body}]`, `normalize_analysis`가 `photo_captions` 보존(자리 유지, 최대 4), `basis_of`가 사진 설명도 근거에 넣음, `build_sections(copy, analysis, name, photos)`, 초안 검사의 최상급 표현 버림, `_settle(sections)`(내용 없는 섹션 제거·고아 split 사진을 갤러리로).

- [ ] **Step 1: 실패하는 테스트 추가·수정** (`tests/test_agent.py`)

`test_normalize_analysis_tolerates_junk`의 `raw`에 `"photo_captions": ["앞", None]`을, 기대값에 `"photo_captions": ["앞", ""]`를 넣는다. 그리고 파일 끝에:

```python
ANALYSIS_L = {**ANALYSIS, "photo_captions": ["정면", "뚜껑을 연 모습", "무광 표면 클로즈업", "손에 쥔 모습"]}
COPY_L = {**COPY, "banner": {"headline": "식은 커피, 아쉽지 않으셨나요?"},
          "details": [{"headline": "한 손으로 여는 뚜껑", "body": "원터치로 열려요."},
                      {"headline": "매끈한 무광 표면", "body": "손자국이 덜 보여요."}]}


def draft_n(folder, n, bad=(), copy=COPY_L):
    fake = JudgeByContent([ANALYSIS_L, copy], bad=bad)
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
    copy_input = fake.json_calls[1][1]["content"]
    assert "사진 2: 뚜껑을 연 모습" in copy_input and "사진 3: 무광 표면 클로즈업" in copy_input


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
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_agent.py -q` / Expected: 7 failed (normalize 1 + 새 6)

- [ ] **Step 3: 구현**

`pagegen/prompts.py`:

- `ANALYZE_SYSTEM`의 `questions` 줄을 바꾸고 `photo_captions` 줄을 더한다:

```text
- photo_captions: 받은 사진 순서대로 사진마다 한 문장. 그 사진에서 보이는 것만 쓴다(예: "뚜껑을 연 모습, 안쪽 스테인리스가 보인다").
- questions: 상세페이지에 꼭 필요한데 설명에 없는 정보를 묻는 질문, 최대 3개. 분류별로 우선 물을 것 — 의류·잡화: 소재(혼용률), 치수, 세탁·관리 방법, 제조국 / 식품: 원재료·원산지, 용량·중량, 보관 방법, 유통기한 / 화장품: 용량, 피부 타입, 주요 성분, 사용 방법 / 가전·전자: 모델명, 정격전압·소비전력, KC 인증, A/S 연락처 / 생활·주방·기타: 소재, 크기, 관리 방법. 설명에 이미 있는 것은 묻지 않는다.
```

- `ANALYZE_SCHEMA`: `required`와 `properties`에 `"photo_captions": _STRINGS` 추가.
- `COPY_SYSTEM`에 더한다:

```text
- hero.headline은 이 제품이 주는 핵심 혜택 하나를 질문형·결과 약속형·비교형 중 하나로, 20자 안에 쓴다. 페이지 전체가 이 메시지 하나를 남기게 한다.
- banner.headline은 고객이 겪는 상황에 공감하는 한 문장이다(질문형 가능). 제품 사실을 새로 말하지 않는다.
- 섹션 제목은 "주요 특징", "이래서 좋아요" 같은 이름표가 아니라 내용이 담긴 문장으로 쓴다.
- 기능을 나열하지 말고 그 기능이 고객에게 주는 혜택을 쓴다.
- "최고, 최상, 최초, 1위, 완벽, 유일, 최저가" 같은 최상급 표현은 판매자 설명에 없으면 쓰지 않는다.
- details는 주어진 사진 설명마다 하나씩, 그 순서대로 쓴다. 사진 설명에 보이는 것만 근거로 하고, headline은 15자 안, body는 한 문장. 사진 설명이 없으면 빈 목록.
```

- `COPY_SCHEMA`: `banner=_object(headline=_T)`, `details={"type": "array", "items": _object(headline=_T, body=_T)}` 추가.

`pagegen/agent.py`:

```python
def basis_of(page: dict, chat: list[dict]) -> str:
    analysis = page["analysis"]
    seen = analysis["visible_features"] + analysis["colors"] + [c for c in analysis.get("photo_captions", []) if c]
    return f"근거 텍스트: {pg.corpus_of(page, chat)}\n사진에서 보이는 것: {', '.join(seen) or '(없음)'}"
```

`normalize_analysis`의 반환 dict에 추가(빈 값도 자리를 지킨다 — 사진 순서와 맞물린다):

```python
            "photo_captions": [str(c or "").strip() for c in raw.get("photo_captions") or []][:4]
            if isinstance(raw.get("photo_captions"), list) else [],
```

`build_sections` 교체:

```python
def build_sections(copy: dict, analysis: dict, name: str, photos: list[dict]) -> tuple[list[dict], str]:
    """Spec §4 (layouts): p1 → hero/scene, p2·p3 → split, p4… → gallery. Returns (sections without ids, scene instruction)."""
    def get(block: str, key: str) -> str:
        part = copy.get(block)
        return str(part.get(key) or "").strip() if isinstance(part, dict) else ""

    uploads = [p["id"] for p in photos if p["origin"] == "upload"]
    points = copy.get("points") if isinstance(copy.get("points"), dict) else {}
    items = [i for i in points.get("items") or [] if isinstance(i, dict) and str(i.get("text") or "").strip()][:3]
    details = [d for d in copy.get("details") or [] if isinstance(d, dict)]
    sections = [
        {"kind": "hero", "variant": "below", "headline": get("hero", "headline") or name, "body": get("hero", "body"),
         "photo": uploads[0]},
        {"kind": "banner", "variant": "light", "headline": get("banner", "headline"), "body": ""},
        {"kind": "points", "variant": "cards", "headline": get("points", "headline") or "이래서 좋아요",
         "body": "\n".join(f"{str(i.get('title') or '').strip()}: {str(i['text']).strip()}" for i in items)},
    ]
    for i, photo in enumerate(uploads[1:3]):
        detail = details[i] if i < len(details) else {}
        sections.append({"kind": "split", "variant": pg.VARIANTS["split"][i % 2],
                         "headline": str(detail.get("headline") or "").strip(),
                         "body": str(detail.get("body") or "").strip(), "photo": photo})
    sections.append({"kind": "scene", "headline": get("scene", "headline"), "body": get("scene", "body"),
                     "photo": uploads[0]})
    if uploads[3:]:
        sections.append({"kind": "gallery", "variant": "grid-2" if len(uploads[3:]) > 1 else "stack",
                         "headline": pg.GALLERY_TITLE, "body": "", "photos": uploads[3:]})
    if analysis["facts"]:
        rows = "\n".join(f"{f['key']}: {f['value']}" for f in analysis["facts"])
        sections.append({"kind": "spec_table", "headline": "제품 정보", "body": rows})
    sections.append({"kind": "notice", "headline": get("notice", "headline") or "안내", "body": get("notice", "body")})
    return sections, get("scene", "photo_instruction") or "제품이 잘 어울리는 밝은 실내 공간"
```

`_clean`: 통과 조건에 최상급 검사를 넣고, 헤드라인 대체값을 종류별로:

```python
    def passes(text: str) -> bool:
        if pg.unsupported_numbers(text, corpus) or pg.unsupported_superlatives(text, corpus):
            return False
        ...  # 나머지는 그대로

    if not passes(section["headline"]):
        section["headline"] = "" if section["kind"] in ("banner", "split") else name
```

`_has_content`를 종류별로 바꾸고 `_settle`을 더한다:

```python
def _has_content(section: dict) -> bool:
    kind = section["kind"]
    if kind == "gallery":
        return bool(section["photos"])
    if kind == "banner":
        return bool(section["headline"])
    if kind == "split":
        return bool(section["headline"] or section["body"])
    return bool(section["body"]) or kind in pg.PHOTO_KINDS


def _settle(sections: list[dict]) -> list[dict]:
    """Drop sections left without content; a dropped split's photo moves to the gallery, never lost."""
    orphans = [s["photo"] for s in sections if s["kind"] == "split" and not _has_content(s)]
    kept = [s for s in sections if _has_content(s)]
    if orphans:
        gallery = next((s for s in kept if s["kind"] == "gallery"), None)
        if gallery is None:
            gallery = {"kind": "gallery", "variant": "stack", "headline": pg.GALLERY_TITLE, "body": "", "photos": []}
            tail = next((i for i, s in enumerate(kept) if s["kind"] in ("spec_table", "notice")), len(kept))
            kept.insert(tail, gallery)
        gallery["photos"] = orphans + gallery["photos"]
        if len(gallery["photos"]) > 1 and gallery["variant"] == "stack":
            gallery["variant"] = "grid-2"
    return kept
```

`make_draft`:
- 문구 입력에 split 사진 설명을 붙인다:

```python
    captions = page["analysis"]["photo_captions"]
    split_lines = [f"사진 {i + 2}: {captions[i + 1]}" for i in range(min(2, len(photos) - 1)) if i + 1 < len(captions) and captions[i + 1]]
    detail_hint = ("details를 쓸 사진 설명:\n" + "\n".join(split_lines)) if split_lines else "사진 설명 없음: details는 빈 목록."
```

  문구 호출의 user 내용을 `f"{seller}\n사진 분석: {json.dumps(page['analysis'], ensure_ascii=False)}\n{detail_hint}"`로 바꾼다.
- `build_sections(copy, page["analysis"], product["name"], photos)`로 호출하고, 앞뒤 거르기 두 줄을 `_settle`로 바꾼다:

```python
    sections = _settle(sections)
    ...
    for section in sections:
        if section["kind"] not in ("spec_table", "gallery"):  # facts are spec-checked; the gallery title is fixed
            _clean(section, corpus, basis, product["name"], llm, tally)
    ...
    page["sections"] = [{"id": f"s{i}", **s} for i, s in enumerate(_settle(sections), 1)]
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q` / Expected: 전체 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat: place every photo (split, gallery) and research-based copy rules in the first draft"`
- [ ] **Step 6: 스펙 §3 반영** — 기반 스펙의 `analysis` 예시에 `photo_captions`를 추가하고 같은 커밋에 넣는다.
- [ ] **Step 7: Codex에게 Task 5 브리프 발행**

---

### Task 4: 변형별 렌더링 (Codex)

**Files:** Modify `pagegen/render.py`, `tests/test_render.py`

**Interfaces — Consumes:** `page.KINDS`, `page.VARIANTS`, `page.split_line`. **Produces:** `<section data-section-id data-kind [data-variant]>`, 새 종류 3개와 변형별 CSS. `render_html`·`export` 시그니처는 그대로.

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_render.py` 끝)

```python
def add(page, **section):
    page["sections"].append(section)
    return page


def test_variant_attribute_and_default(page):
    page["sections"][0]["variant"] = "nope"  # unknown → default
    html = html_of(add(page, id="s4", kind="scene", headline="장면", body="", photo="p1"))
    assert '<section data-section-id="s1" data-kind="hero" data-variant="below">' in html
    assert '<section data-section-id="s4" data-kind="scene">' in html  # kinds without variants get no attribute
    assert 'data-kind="points" data-variant="cards"' in html  # old page.json without variant still renders


def test_split_and_banner(page):
    add(page, id="s4", kind="split", variant="image-right", headline="한 손으로", body="누르면 열려요", photo="p1")
    add(page, id="s5", kind="banner", variant="dark", headline="<식은 커피>", body="")
    html = html_of(page)
    assert 'data-kind="split" data-variant="image-right"' in html and 'class="split-photo"' in html
    assert 'data-kind="banner" data-variant="dark"' in html and "&lt;식은 커피&gt;" in html


def test_gallery_renders_known_photos_only(page):
    html = html_of(add(page, id="s4", kind="gallery", variant="grid-3", headline="더 많은 모습", body="",
                       photos=["p1", "p9"]))
    gallery = html[html.index('data-kind="gallery"'):]
    assert gallery.count("<img") == 1 and 'data-count="1"' in gallery


def test_numbered_points_and_overlay_hero(page):
    page["sections"][0]["variant"] = "overlay"
    page["sections"][1]["variant"] = "numbered"
    html = html_of(page)
    assert 'data-variant="overlay"' in html and 'class="hero-copy"' in html
    assert 'data-kind="points" data-variant="numbered"' in html
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_render.py -q` / Expected: 4 failed

- [ ] **Step 3: `pagegen/render.py` 수정**

`from .page import KINDS, VARIANTS, split_line`로 바꾸고, `_section`을 아래처럼 고친다(나머지 함수는 그대로):

```python
def _img(photos: dict[str, str], photo_id, photo_url) -> str:
    return f'<img src="{escape(photo_url(photos[photo_id]))}" alt="">' if photo_id in photos else ""


def _section(section: dict, photos: dict[str, str], photo_url: Callable[[str], str]) -> str:
    kind = section["kind"] if section["kind"] in KINDS else "notice"
    allowed = VARIANTS.get(kind)
    variant = (section.get("variant") if section.get("variant") in allowed else allowed[0]) if allowed else None
    headline, body = escape(section["headline"]), section["body"]
    image = _img(photos, section.get("photo"), photo_url)
    if kind == "hero":
        inner = f'<div class="hero-media">{image}<div class="hero-copy"><h1>{headline}</h1><p>{escape(body)}</p></div></div>'
    elif kind == "banner":
        inner = f"<h2>{headline}</h2>" + (f"<p>{escape(body)}</p>" if body.strip() else "")
    elif kind == "split":
        inner = (f'<div class="split-photo">{image}</div>'
                 f'<div class="split-text"><h2>{headline}</h2><p>{escape(body)}</p></div>')
    elif kind == "scene":
        inner = f"{image}<h2>{headline}</h2><p>{escape(body)}</p>"
    elif kind == "gallery":
        images = [_img(photos, pid, photo_url) for pid in section.get("photos", [])]
        images = [i for i in images if i]
        inner = f'<h2>{headline}</h2><div class="grid" data-count="{len(images)}">{"".join(images)}</div>'
    elif kind == "points":
        items = "".join(f"<li><strong>{escape(k)}</strong><span>{escape(v)}</span></li>"
                        for k, v in map(split_line, _lines(body)))
        inner = f"<h2>{headline}</h2><ul>{items}</ul>"
    elif kind == "spec_table":
        rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in map(split_line, _lines(body)))
        inner = f"<h2>{headline}</h2><table>{rows}</table>"
    else:
        inner = f"<h2>{headline}</h2>" + "".join(f"<p>{escape(line)}</p>" for line in _lines(body))
    attrs = f'data-section-id="{escape(section["id"])}" data-kind="{kind}"' + (f' data-variant="{variant}"' if variant else "")
    return f"<section {attrs}>{inner}</section>"
```

`CSS` 끝에 추가(기존 hero 규칙 `section[data-kind=hero] h1/p`는 `.hero-copy` 안에서도 그대로 맞는다):

```css
.hero-media{position:relative}
section[data-variant=overlay] .hero-copy{position:absolute;left:0;right:0;bottom:0;padding:140px 0 48px;background:linear-gradient(transparent,rgba(0,0,0,.6))}
section[data-variant=overlay] .hero-copy h1{margin:0 64px 12px;color:#fff}
section[data-variant=overlay] .hero-copy p{color:#eee}
section[data-kind=banner]{padding:96px 64px}
section[data-kind=banner] h2{font-size:36px;line-height:1.4;margin:0}
section[data-kind=banner] p{margin-top:16px;font-size:18px;color:#555}
section[data-variant=dark]{background:#121B29;color:#fff}
section[data-variant=dark] p{color:#d0d4da}
section[data-variant=numbered] ul{counter-reset:item}
section[data-variant=numbered] li{counter-increment:item;position:relative;padding-left:104px}
section[data-variant=numbered] li::before{content:counter(item,decimal-leading-zero);position:absolute;left:32px;top:26px;font-size:34px;font-weight:700;color:#0a66ff}
section[data-kind=split]{display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:center;text-align:left}
section[data-variant=image-right] .split-photo{order:2}
.split-photo{aspect-ratio:1;background:#f5f5f7;border-radius:16px;overflow:hidden}
.split-photo img{width:100%;height:100%;object-fit:contain}
.split-text h2{font-size:28px;margin-bottom:16px}
.split-text p{font-size:18px;color:#555;line-height:1.7}
section[data-kind=gallery] .grid{display:grid;gap:12px;grid-template-columns:1fr}
section[data-variant=grid-2] .grid{grid-template-columns:1fr 1fr}
section[data-variant=grid-3] .grid{grid-template-columns:1fr 1fr 1fr}
section[data-kind=gallery] .grid[data-count="1"]{grid-template-columns:1fr}
section[data-variant=grid-2] img,section[data-variant=grid-3] img{aspect-ratio:1;object-fit:contain;background:#f5f5f7;border-radius:12px}
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q` / Expected: 전체 통과(내보내기 테스트 포함)
- [ ] **Step 5: 샘플 렌더** — 변형을 섞은 9섹션 페이지를 `export`로 PNG를 만들어 `.orchestration/reports/2026-10-16-layouts/`에 두고, 섹션마다 깨진 데가 없는지 육안으로 확인해 보고한다. CSS는 위 기준에서 다듬어도 된다(테스트 유지, 외부 리소스 없음, 본문 17px 이상).
- [ ] **Step 6: 보고** — Claude가 검수 후 커밋한다.

---

### Task 5: 실모델 평가 갱신 (Codex)

**Files:** Modify `scripts/eval_tools.py`; 필요하면 `pagegen/prompts.py`의 문구만(`CHAT_RULES`, 도구 `description`)

- [ ] **Step 1:** `PAGE`의 `photos`에 `{"id": "p2", "file": "photos/p2.jpg", "origin": "upload"}`, `sections` 끝에 `{"id": "s4", "kind": "gallery", "variant": "grid-2", "headline": "더 많은 모습", "body": "", "photos": ["p2"]}`를 넣고, `TOOL_CASES`에 추가:

```python
    ("히어로 글씨를 사진 위에 올려줘", lambda c: has(c, "set_variant", section_id="s1", variant="overlay")),
    ("갤러리를 3열로 바꿔줘", lambda c: has(c, "set_variant", section_id="s4", variant="grid-3")),
```

  마지막 줄의 기준을 `tool_ok >= len(TOOL_CASES) - 2`(13건이면 11)로 바꾼다.
- [ ] **Step 2:** `uv run python scripts/eval_tools.py | tee .orchestration/reports/2026-10-16-layouts/eval-baseline.txt`
- [ ] **Step 3:** 목표(도구 ≥ 11/13, 판정 8/8) 미달이면 실패 사례로 `CHAT_RULES`·도구 설명 문구를 고쳐 최대 3회 다시 잰다. 단위 테스트(`uv run pytest -q`)는 매번 통과해야 한다. 평가 문장을 프롬프트에 그대로 넣지 않는다.
- [ ] **Step 4: 보고** — 기준선·최종 점수, 바꾼 문구와 이유, 남은 실패 원문. Claude가 다시 돌려 확인한 뒤 커밋한다.

---

### Task 6: 사진 4장 스모크 (Codex)

**Files:** Modify `scripts/smoke.py`

- [ ] **Step 1:** 인자 `--extra`(사진 경로 여러 개)를 더하고, 리포트 저장 직전에 아래 블록을 넣는다:

```python
    if args.extra:
        files = [("photos", (p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/jpeg"))
                 for p in [args.photo, *args.extra]]
        r, seconds = timed(client.post, "/projects", data={"name": args.name, "notes": args.notes}, files=files)
        entry = {"status": r.status_code, "draft_seconds": seconds, "body": r.json()}
        report["multi"] = entry
        print(f"multi ({len(files)} photos): draft {r.status_code} in {seconds}s", flush=True)
        if r.status_code == 200:
            pid = entry["body"]["id"]
            page = client.get(f"/projects/{pid}").json()["page"]
            used = {s.get("photo") for s in page["sections"]} | {p for s in page["sections"] for p in s.get("photos", [])}
            uploads = [p["id"] for p in page["photos"] if p["origin"] == "upload"]
            entry["layout"] = [(s["kind"], s.get("variant"), s.get("photo") or s.get("photos")) for s in page["sections"]]
            entry["unused_uploads"] = [u for u in uploads if u not in used]
            failed |= bool(entry["unused_uploads"])
            r = client.post(f"/projects/{pid}/export")
            if r.status_code == 200:
                (args.out / "multi-page.png").write_bytes(client.get(r.json()["png"]).content)
            failed |= r.status_code != 200
        else:
            failed = True
```

  (`import mimetypes` 추가.)
- [ ] **Step 2:** 서버를 띄우고(`:8000`이 이미 떠 있으면 그대로 쓴다) 사진 4장으로 실행: `uv run python scripts/smoke.py --photo <p1> --extra <p2> <p3> <p4> --name … --notes … --out .orchestration/reports/2026-10-16-layouts/smoke`. 사진은 `../page-generation-improve/data/evaluation/cma_real_v1/images/`의 CC0 사진 4장.
- [ ] **Step 3: 보고** — 초안 시간(≤120초), `layout`, `unused_uploads`(빈 목록이어야 함), `multi-page.png` 육안 소감(섹션별), 문구에 최상급·근거 없는 주장이 남았는지 원문 인용. Claude가 검수 후 커밋한다.

---

## 완료 조건

- `uv run pytest -q` 전체 통과
- `scripts/eval_tools.py`: 도구 ≥ 11/13, 판정 8/8
- 사진 4장 스모크: 초안 ≤120초, 올린 사진 전부 페이지에 있음
- 기반 스펙 §3에 `photo_captions` 반영
