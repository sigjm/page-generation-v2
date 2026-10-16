# 대화형 상세페이지 에이전트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사진과 설명으로 상세페이지 초안을 만들고, 채팅으로 고친 뒤, 폭 860px 이미지로 내보내는 로컬 웹 에이전트를 만든다.

**Architecture:** FastAPI 하나가 업로드·채팅·미리보기·내보내기를 맡는다. 첫 초안은 정해진 순서로 만들고(분석 → 문구 → 검사 → 연출 컷), 채팅 수정은 Qwen3.8-9B의 도구 호출 7개가 `page.json`을 바꾼다. HTML은 `page.json`에서 결정적으로 렌더링하고, Playwright로 캡처한다. 모델은 이미 떠 있는 로컬 MLX Serve만 쓴다.

**Tech Stack:** Python 3.13, uv, FastAPI, uvicorn, python-multipart, Pillow, Playwright(Chromium), pytest, httpx(테스트용), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-16-conversational-detail-page-agent-design.md`

## Global Constraints

- Python `>=3.13`, 패키지 관리는 uv. 런타임 의존성은 `fastapi`, `uvicorn`, `python-multipart`, `pillow`, `playwright`만, 개발 의존성은 `pytest`, `httpx`만.
- 패키지는 저장소 루트의 `pagegen/` 하나. `src/` 레이아웃을 쓰지 않는다.
- 외부 호출 없음. HTTP는 `MLX_URL`(기본 `http://127.0.0.1:11234`)로만 보낸다. CDN·웹폰트를 쓰지 않는다. 폰트는 `-apple-system, "Apple SD Gothic Neo", sans-serif`.
- 서버는 `127.0.0.1:8000`에만 바인딩한다.
- 환경 변수 기본값: `TEXT_MODEL=empero-ai/Qwen3.8-9B-Distill-MLX-4bit`, `IMAGE_MODEL=ddalcu/Qwen-Image-2.1-MLX-Serve-4bit`, `IMAGE_STEPS=20`, `PROJECTS_DIR=projects`.
- 모델이 쓴 문자열은 HTML에 넣기 전에 모두 `html.escape`를 거친다.
- 업로드 원본 파일은 절대 덮어쓰지 않는다. 편집 결과는 항상 새 파일(`photos/<원본id>-e<n>.png`)이다.
- 사용자에게 보이는 문구(오류·안내·UI)는 한국어.
- `page.json` 모양은 스펙 §3이 기준이다.
- Codex는 git 상태를 바꾸지 않는다. 커밋은 Claude가 검수 뒤에 한다.
- 실행 중인 MLX Serve(:11234)를 재시작·종료하지 않는다.

## Review Focus

1. **도구 인자가 깨진 JSON이거나 필수 인자가 빠진 호출** → 500이 아니라 도구 오류 문장이 모델에게 돌아가야 한다. (Task 4: `test_malformed_arguments_are_a_tool_error`, `test_missing_argument`)
2. **thinking을 켠 응답의 본문에 `<think>…</think>`가 섞여 오는 경우** → 채팅 답에 추론 내용이 보이면 안 된다. (Task 3: `test_chat_strips_inline_think`)
3. **휴대폰 세로 사진(EXIF 회전)** → 모델과 편집 입력은 똑바로 선 사진을 받아야 한다. (Task 3: `test_exif_rotation_applied`)
4. **배경이 투명한 PNG 누끼 사진** → 모델·편집 입력에서 배경이 검정이 아니라 흰색이어야 한다. (Task 3: `test_transparent_png_flattens_to_white`)
5. **`"용량:500ml"`(콜론 뒤 공백 없음)이나 값에 `:`가 든 줄(`"10:30"`)** → 근거 검사와 렌더러가 같은 방식으로 나눠야 한다. (Task 1: `test_split_line_tolerates_missing_space`, Task 6: `test_points_and_spec_table_split_lines`)

## 파일 구조

| 파일 | 맡는 일 | 담당 | 작업 |
|---|---|---|---|
| `pyproject.toml` | 의존성·pytest 설정 | Claude | 1 |
| `pagegen/__init__.py` | 빈 패키지 표시 | Claude | 1 |
| `pagegen/page.py` | `page.json` 순수 연산: 도구 6개의 상태 변경, 숫자·스펙 표 검사, 줄 나누기 | Claude | 1 |
| `pagegen/store.py` | 프로젝트 폴더 입출력: 업로드 검증·저장, page/chat 읽기·쓰기, 버전·되돌리기, 내보낼 파일 경로 검사 | Claude | 2 |
| `pagegen/llm.py` | MLX Serve 클라이언트: chat(도구·비전·json_schema), JSON 파싱, 사진 전처리, Qwen-Image 편집 | Claude | 3 |
| `pagegen/prompts.py` | 시스템 프롬프트·JSON 스키마·도구 정의(평가 작업에서 Codex가 문구를 다듬는다) | Claude → Codex | 4, 9 |
| `pagegen/agent.py` | 채팅 도구 루프, 주장 검사, 사진 편집, 첫 초안 파이프라인 | Claude | 4, 5 |
| `pagegen/render.py` | `page.json` → HTML, Playwright 내보내기 | Codex | 6 |
| `static/index.html` | 채팅 + 미리보기 화면 | Codex | 7 |
| `pagegen/server.py` | FastAPI 라우트, 전역 모델 잠금, 오류 → HTTP 상태 | Claude | 8 |
| `scripts/eval_tools.py` | 실모델 도구·주장 검사 회귀 측정 | Codex | 9 |
| `scripts/smoke.py`, `README.md` | 실서버 처음부터 끝까지 확인, 실행 안내 | Codex | 10 |
| `tests/conftest.py` | 공용 `page` 픽스처 | Claude | 1 |
| `tests/test_*.py` | 작업별 단위 테스트 | 각 작업 담당 | 1–8 |

스펙 §2와 다른 점 하나: 파일 입출력을 `page.py`에서 `store.py`로 떼어 냈다. `page.py`가 파일 없이 테스트되도록 하기 위해서다. 공용 계약(`page.json`, `render_html(page, photo_url)`)은 바뀌지 않는다.

## 순서와 분담

```text
Claude: 1 → 2 → 3 → 4 → 5 ─────────────→ 8 ──→ (검수·커밋)
Codex:      └→ 6 (render) ┐                  │
            └→ 7 (UI)     ┘ (1 끝나면 바로)    ├→ 9 (eval, 4·5 뒤)
                                             └→ 10 (smoke, 7·8 뒤)
```

- Task 1이 끝나면 Codex에게 6·7을 브리프로 보낸다(서로 다른 파일, 계약은 스펙 §3·§7).
- Codex 작업은 `.orchestration/tasks/<날짜>-<주제>.md` 브리프로 보낸다. 브리프에는 이 계획의 해당 Task 전문을 붙인다. 결과는 Claude가 `git diff`와 `uv run pytest`로 확인한 뒤 커밋한다.

---

### Task 1: 프로젝트 뼈대와 `page.py` (Claude)

**Files:**
- Create: `pyproject.toml`, `pagegen/__init__.py`, `pagegen/page.py`, `tests/conftest.py`, `tests/test_page.py`

**Interfaces:**
- Consumes: 없음
- Produces (`pagegen.page`):
  - `KINDS: tuple[str, ...] = ("hero", "points", "scene", "spec_table", "notice")`, `PHOTO_KINDS = ("hero", "scene")`
  - `class ToolError(Exception)` — 메시지가 그대로 모델에게 돌아간다
  - `corpus_of(page: dict, chat: list[dict]) -> str`
  - `numbers(text: str) -> set[tuple[str, str]]`, `unsupported_numbers(text: str, corpus: str) -> list[str]`
  - `split_line(line: str) -> tuple[str, str]`, `is_sourced(value: str, corpus: str) -> bool`, `unsupported_spec_values(body: str, corpus: str) -> list[str]`
  - `check_text(text: str, corpus: str, *, spec: bool = False) -> None` (raises `ToolError`)
  - `find_section(page, section_id) -> dict`, `find_photo(page, photo_id) -> dict`, `next_section_id(page) -> str`
  - `update_text(page, section_id, field, text, corpus, verify=…) -> str`
  - `add_section(page, kind, after_section_id, headline, body, corpus, verify=…) -> str`
  - `remove_section(page, section_id) -> str`, `move_section(page, section_id, after_section_id) -> str`
  - `use_photo(page, section_id, photo_id) -> str`, `edit_source(page, section_id) -> dict`, `add_edit(page, source_id, instruction) -> dict`
  - `verify`는 `Callable[[str], None]`. 숫자 검사를 통과한 새 문구로 호출되고, 거절하려면 `ToolError`를 던진다.
- 테스트 픽스처 `page`(`tests/conftest.py`): 아래 Step 1의 텀블러 페이지. 다른 작업의 테스트도 이 픽스처를 쓴다.

- [ ] **Step 1: 뼈대와 실패하는 테스트 작성**

`pyproject.toml`:

```toml
[project]
name = "pagegen"
version = "0.1.0"
description = "로컬 대화형 상세페이지 에이전트"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9",
    "pillow>=11.0",
    "playwright>=1.48",
]

[dependency-groups]
dev = ["pytest>=8.3", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

`pagegen/__init__.py`: 빈 파일.

`tests/conftest.py`:

```python
import copy

import pytest

PAGE = {
    "id": "20261016-153012-ab12",
    "version": 1,
    "product": {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종"},
    "analysis": {
        "category": "주방용품",
        "visible_features": ["원터치 뚜껑"],
        "colors": ["실버"],
        "facts": [{"key": "용량", "value": "500ml"}],
        "questions": [],
    },
    "photos": [
        {"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
        {"id": "p1-e1", "file": "photos/p1-e1.png", "origin": "edit", "source": "p1", "instruction": "대리석"},
    ],
    "sections": [
        {"id": "s1", "kind": "hero", "headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러", "photo": "p1-e1"},
        {"id": "s2", "kind": "points", "headline": "이래서 좋아요", "body": "넉넉한 용량: 500ml"},
        {"id": "s3", "kind": "notice", "headline": "관리 방법", "body": ""},
    ],
}


@pytest.fixture
def page():
    return copy.deepcopy(PAGE)
```

`tests/test_page.py`:

```python
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
        pg.add_section(page, "banner", "s1", "", "", CORPUS)
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv sync && uv run pytest tests/test_page.py -q`
Expected: FAIL — `ImportError: cannot import name 'page' from 'pagegen'`

- [ ] **Step 3: `pagegen/page.py` 구현**

```python
"""Page state (page.json, spec §3) and the pure parts of the chat tools. No file or model access."""
import re
from collections.abc import Callable

KINDS = ("hero", "points", "scene", "spec_table", "notice")
PHOTO_KINDS = ("hero", "scene")
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-zA-Z%℃°]+)?")


class ToolError(Exception):
    """A tool failure; its message goes back to the model as the tool result."""


def _no_check(text: str) -> None:
    pass


def corpus_of(page: dict, chat: list[dict]) -> str:
    """Grounding text (spec §5): product name, seller notes, every message the user typed."""
    product = page["product"]
    return "\n".join([product["name"], product["notes"], *(m["text"] for m in chat if m["role"] == "user")])


def numbers(text: str) -> set[tuple[str, str]]:
    return {(n.replace(",", "."), unit.lower()) for n, unit in _NUMBER.findall(text)}


def unsupported_numbers(text: str, corpus: str) -> list[str]:
    # ponytail: units are Latin letters, % and ℃ only; "3시간" compares the bare number and the claim check covers the rest
    have = numbers(corpus)
    bare = {n for n, _ in have}
    return sorted(n + unit for n, unit in numbers(text) if ((n, unit) not in have if unit else n not in bare))


def split_line(line: str) -> tuple[str, str]:
    """'제목: 설명' → ('제목', '설명'); splits at the first ':' so '10:30' values survive."""
    key, sep, value = line.partition(":")
    return (key.strip(), value.strip()) if sep else ("", line.strip())


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def is_sourced(value: str, corpus: str) -> bool:
    return _squash(value) in _squash(corpus)


def unsupported_spec_values(body: str, corpus: str) -> list[str]:
    values = (split_line(line)[1] for line in body.splitlines() if line.strip())
    return [v for v in values if not is_sourced(v, corpus)]


def check_text(text: str, corpus: str, *, spec: bool = False) -> None:
    bad = unsupported_numbers(text, corpus)
    if bad:
        raise ToolError(f"오류: 근거 없는 숫자 {', '.join(bad)}. 사용자에게 ask_user로 물어보세요.")
    bad = unsupported_spec_values(text, corpus) if spec else []
    if bad:
        raise ToolError(f"오류: 근거 없는 스펙 값 {', '.join(bad)}. 사용자에게 ask_user로 물어보세요.")


def _index(page: dict, section_id: str) -> int:
    for i, section in enumerate(page["sections"]):
        if section["id"] == section_id:
            return i
    known = ", ".join(s["id"] for s in page["sections"])
    raise ToolError(f"오류: 없는 섹션 {section_id}. 있는 섹션: {known}")


def find_section(page: dict, section_id: str) -> dict:
    return page["sections"][_index(page, section_id)]


def find_photo(page: dict, photo_id: str) -> dict:
    for photo in page["photos"]:
        if photo["id"] == photo_id:
            return photo
    known = ", ".join(p["id"] for p in page["photos"])
    raise ToolError(f"오류: 없는 사진 {photo_id}. 있는 사진: {known}")


def next_section_id(page: dict) -> str:
    return f"s{max((int(s['id'][1:]) for s in page['sections']), default=0) + 1}"


def update_text(page: dict, section_id: str, field: str, text: str, corpus: str,
                verify: Callable[[str], None] = _no_check) -> str:
    section = find_section(page, section_id)
    if field not in ("headline", "body"):
        raise ToolError("오류: field는 headline 또는 body만 됩니다.")
    check_text(text, corpus, spec=section["kind"] == "spec_table" and field == "body")
    verify(text)
    section[field] = text
    return f"{section_id} {field} 변경"


def add_section(page: dict, kind: str, after_section_id: str, headline: str, body: str, corpus: str,
                verify: Callable[[str], None] = _no_check) -> str:
    if kind not in KINDS:
        raise ToolError(f"오류: kind는 {', '.join(KINDS)} 중 하나입니다.")
    position = _index(page, after_section_id) + 1 if after_section_id else 0
    check_text(headline, corpus)
    check_text(body, corpus, spec=kind == "spec_table")
    verify(f"{headline}\n{body}")
    section = {"id": next_section_id(page), "kind": kind, "headline": headline, "body": body}
    if kind in PHOTO_KINDS:
        section["photo"] = page["photos"][0]["id"]
    page["sections"].insert(position, section)
    return f"{section['id']}({kind}) 추가"


def remove_section(page: dict, section_id: str) -> str:
    page["sections"].pop(_index(page, section_id))
    return f"{section_id} 삭제"


def move_section(page: dict, section_id: str, after_section_id: str) -> str:
    if section_id == after_section_id:
        raise ToolError("오류: 섹션을 자기 자신 뒤로 옮길 수 없습니다.")
    index = _index(page, section_id)
    if after_section_id:
        _index(page, after_section_id)  # unknown anchor fails before anything moves
    section = page["sections"].pop(index)
    position = _index(page, after_section_id) + 1 if after_section_id else 0
    page["sections"].insert(position, section)
    return f"{section_id}를 {after_section_id + ' 다음' if after_section_id else '맨 앞'}으로 이동"


def _photo_section(page: dict, section_id: str) -> dict:
    section = find_section(page, section_id)
    if section["kind"] not in PHOTO_KINDS:
        raise ToolError(f"오류: {section['kind']} 섹션에는 사진이 없습니다. 사진은 hero·scene에만 있습니다.")
    return section


def use_photo(page: dict, section_id: str, photo_id: str) -> str:
    section = _photo_section(page, section_id)
    find_photo(page, photo_id)
    section["photo"] = photo_id
    return f"{section_id} 사진을 {photo_id}로 교체"


def edit_source(page: dict, section_id: str) -> dict:
    """The uploaded original behind a photo section; edits always start from it (spec §4.2)."""
    photo = find_photo(page, _photo_section(page, section_id)["photo"])
    return find_photo(page, photo.get("source", photo["id"]))


def add_edit(page: dict, source_id: str, instruction: str) -> dict:
    n = 1 + sum(1 for p in page["photos"] if p.get("source") == source_id)
    photo = {"id": f"{source_id}-e{n}", "file": f"photos/{source_id}-e{n}.png", "origin": "edit",
             "source": source_id, "instruction": instruction}
    page["photos"].append(photo)
    return photo
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_page.py -q`
Expected: `16 passed`

- [ ] **Step 5: 커밋**

```bash
git add pyproject.toml uv.lock pagegen/__init__.py pagegen/page.py tests/conftest.py tests/test_page.py
git commit -m "feat: page state, chat tool mutations and number/spec checks"
```

- [ ] **Step 6: Codex에게 Task 6·7 브리프 발행** — 이 계획의 Task 6, Task 7 전문을 각각 `.orchestration/tasks/2026-10-16-render.md`, `.orchestration/tasks/2026-10-16-ui.md`에 붙이고 하지 말 것(git 상태 변경, 담당 밖 파일 수정, MLX Serve 재시작)과 보고 형식을 적어 한 건씩 보낸다. 6을 먼저 보내고, 6이 끝나면 7을 보낸다(같은 워커가 한 번에 하나씩).

---

### Task 2: `store.py` — 프로젝트 폴더 (Claude)

**Files:**
- Create: `pagegen/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: 없음
- Produces (`pagegen.store`):
  - `ROOT: Path` (테스트에서 monkeypatch), `MAX_PHOTOS = 6`, `MAX_BYTES = 20 MiB`, `MAX_PIXELS = 40_000_000`
  - `class NotFound(Exception)`, `class BadInput(Exception)`(메시지를 사용자에게 그대로 보인다), `class NothingToUndo(Exception)`
  - `path(pid: str) -> Path` (id 형식이 아니면 `NotFound`)
  - `create(photos: list[bytes]) -> tuple[str, list[dict]]` — 검증 후 원본 바이트 저장, 사진 레코드 `[{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"}, …]`
  - `load(pid) -> tuple[dict, list[dict]]`, `save(pid, page, chat, snapshot=None) -> None`, `save_chat(pid, chat) -> None`
  - `undo(pid) -> int` (복원한 version)
  - `served_file(pid, rel: str) -> Path` (`rel` 예: `"photos/p1.jpg"`, `"export/sections/01-hero.png"`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_store.py`:

```python
import io
import json

import pytest
from PIL import Image

from pagegen import store


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    return tmp_path


def image_bytes(fmt="JPEG", size=(40, 30), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, "red").save(buffer, fmt)
    return buffer.getvalue()


def make_project():
    pid, photos = store.create([image_bytes()])
    page = {"id": pid, "version": 1, "photos": photos, "sections": []}
    store.save(pid, page, [])
    return pid, page


def test_create_saves_original_bytes_with_detected_extension(root):
    jpg, png = image_bytes("JPEG"), image_bytes("PNG", mode="RGBA")
    pid, photos = store.create([jpg, png])
    assert photos == [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
                      {"id": "p2", "file": "photos/p2.png", "origin": "upload"}]
    assert (root / pid / "photos/p1.jpg").read_bytes() == jpg


@pytest.mark.parametrize("payload, message", [
    ([b"not an image"], "JPG·PNG·WebP"),
    ([image_bytes("GIF")], "JPG·PNG·WebP"),
    ([], "1~6장"),
    ([image_bytes()] * 7, "1~6장"),
])
def test_create_rejects_bad_uploads_before_writing(root, payload, message):
    with pytest.raises(store.BadInput, match=message):
        store.create(payload)
    assert list(root.iterdir()) == []


def test_create_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(store, "MAX_BYTES", 10)
    with pytest.raises(store.BadInput, match="20MB"):
        store.create([image_bytes()])


def test_create_rejects_too_many_pixels(monkeypatch):
    monkeypatch.setattr(store, "MAX_PIXELS", 100)
    with pytest.raises(store.BadInput, match="해상도"):
        store.create([image_bytes(size=(20, 20))])


@pytest.mark.parametrize("pid", ["../etc", "20261016-153012-ab1", "x", "20261016-153012-ab12/.."])
def test_path_rejects_bad_ids(pid):
    with pytest.raises(store.NotFound):
        store.path(pid)


def test_load_missing_project():
    with pytest.raises(store.NotFound):
        store.load("20261016-153012-ab12")


def test_save_snapshot_and_undo():
    pid, page = make_project()
    before = json.loads(json.dumps(page))
    page["version"] = 2
    page["sections"].append({"id": "s1"})
    store.save(pid, page, [{"role": "user", "text": "hi"}], snapshot=before)
    assert store.undo(pid) == 1
    restored, chat = store.load(pid)
    assert restored["sections"] == [] and chat == [{"role": "user", "text": "hi"}]
    with pytest.raises(store.NothingToUndo):
        store.undo(pid)


def test_served_file_only_listed_photos_and_export(root):
    pid, _ = make_project()
    assert store.served_file(pid, "photos/p1.jpg").name == "p1.jpg"
    (root / pid / "photos/secret.jpg").write_bytes(b"x")
    (root / pid / "export/sections").mkdir(parents=True)
    (root / pid / "export/sections/01-hero.png").write_bytes(b"x")
    assert store.served_file(pid, "export/sections/01-hero.png").name == "01-hero.png"
    for rel in ["photos/secret.jpg", "page.json", "export/../page.json", "photos/../page.json", "export/nope.png", "export"]:
        with pytest.raises(store.NotFound):
            store.served_file(pid, rel)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'store'`

- [ ] **Step 3: `pagegen/store.py` 구현**

```python
"""Project folders on disk (spec §3): page.json, versions/, chat.json, photos/, export/."""
import io
import json
import os
import re
import secrets
import time
from pathlib import Path

from PIL import Image

ROOT = Path(os.environ.get("PROJECTS_DIR", "projects"))
MAX_PHOTOS = 6
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses to even open anything past 2x this
FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
_NAME = re.compile(r"[\w.-]+")
_NOT_A_PHOTO = "JPG·PNG·WebP 사진만 올릴 수 있습니다. HEIC는 JPG로 변환해 올려 주세요."


class NotFound(Exception):
    pass


class BadInput(Exception):
    """The message is shown to the user as-is."""


class NothingToUndo(Exception):
    pass


def path(pid: str) -> Path:
    if not _ID.fullmatch(pid):
        raise NotFound(pid)
    return ROOT / pid


def _extension(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise BadInput("사진은 한 장에 20MB까지 올릴 수 있습니다.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt, pixels = im.format, im.width * im.height
            im.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise BadInput(_NOT_A_PHOTO) from e
    if fmt not in FORMATS:
        raise BadInput(_NOT_A_PHOTO)
    if pixels > MAX_PIXELS:
        raise BadInput("사진 해상도가 너무 큽니다(4,000만 화소까지).")
    return FORMATS[fmt]


def create(photos: list[bytes]) -> tuple[str, list[dict]]:
    """Validate every upload first, then save the original bytes untouched (spec §4.1-1)."""
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise BadInput(f"사진은 1~{MAX_PHOTOS}장 올려 주세요.")
    extensions = [_extension(data) for data in photos]
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    (ROOT / pid / "photos").mkdir(parents=True)
    records = []
    for i, (data, ext) in enumerate(zip(photos, extensions), 1):
        record = {"id": f"p{i}", "file": f"photos/p{i}.{ext}", "origin": "upload"}
        (ROOT / pid / record["file"]).write_bytes(data)
        records.append(record)
    return pid, records


def _read(file: Path):
    return json.loads(file.read_text("utf-8"))


def _write(file: Path, value) -> None:
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(file)


def load(pid: str) -> tuple[dict, list[dict]]:
    folder = path(pid)
    try:
        page = _read(folder / "page.json")
    except FileNotFoundError:
        raise NotFound(pid) from None
    chat = _read(folder / "chat.json") if (folder / "chat.json").exists() else []
    return page, chat


def save(pid: str, page: dict, chat: list[dict], snapshot: dict | None = None) -> None:
    """Write page.json and chat.json; `snapshot` (the page before this turn) goes to versions/ for undo."""
    folder = path(pid)
    if snapshot is not None:
        (folder / "versions").mkdir(exist_ok=True)
        _write(folder / "versions" / f"page.{snapshot['version']}.json", snapshot)
    _write(folder / "page.json", page)
    save_chat(pid, chat)


def save_chat(pid: str, chat: list[dict]) -> None:
    _write(path(pid) / "chat.json", chat)


def undo(pid: str) -> int:
    load(pid)  # unknown project → NotFound
    folder = path(pid)
    versions = sorted((folder / "versions").glob("page.*.json"), key=lambda f: int(f.name.split(".")[1]))
    if not versions:
        raise NothingToUndo(pid)
    page = _read(versions[-1])
    _write(folder / "page.json", page)
    versions[-1].unlink()
    return page["version"]


def served_file(pid: str, rel: str) -> Path:
    """A file the browser may fetch: a photo listed in page.json, or a file under export/ (spec §7)."""
    page, _ = load(pid)
    folder = path(pid)
    parts = rel.split("/")
    if not all(_NAME.fullmatch(part) and part not in (".", "..") for part in parts):
        raise NotFound(rel)
    file = (folder / rel).resolve()
    listed = rel in {p["file"] for p in page["photos"]}
    exported = parts[0] == "export" and file.is_relative_to((folder / "export").resolve())
    if not (listed or exported) or not file.is_file():
        raise NotFound(rel)
    return file
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_store.py -q`
Expected: `14 passed`

- [ ] **Step 5: 커밋**

```bash
git add pagegen/store.py tests/test_store.py
git commit -m "feat: project folders with upload checks, versions and undo"
```

---

### Task 3: `llm.py` — MLX Serve 클라이언트 (Claude)

**Files:**
- Create: `pagegen/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: 없음
- Produces (`pagegen.llm`):
  - `MLX_URL`, `TEXT_MODEL`, `IMAGE_MODEL`, `IMAGE_STEPS` (환경 변수에서)
  - `class LLMError(Exception)` — 메시지를 사용자에게 보인다
  - `chat(messages, *, tools=None, schema=None, thinking=False, max_tokens=1024, temperature=0.0) -> dict` — OpenAI `message` 객체(`content`, `tool_calls`). 본문의 `<think>…</think>`는 지운다
  - `parse_json(text) -> dict` (코드 펜스 제거, 객체가 아니면 `ValueError`)
  - `chat_json(messages, schema, *, max_tokens=1500) -> dict` — thinking 끔, 실패하면 temperature 0.3으로 한 번 더, 또 실패하면 `LLMError`
  - `model_jpeg(file: Path, max_side=1280) -> bytes`, `edit_input(file: Path, size=1024) -> bytes`, `image_part(jpeg: bytes) -> dict`
  - `edit_image(file: Path, instruction: str) -> bytes` (PNG 바이트)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_llm.py`:

```python
import io
import urllib.error

import pytest
from PIL import Image

from pagegen import llm


def save(tmp_path, im, name="p.png", **kw):
    file = tmp_path / name
    im.save(file, **kw)
    return file


def test_parse_json_strips_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('{"a": 1}') == {"a": 1}
    for bad in ["[1]", "", None, "not json"]:
        with pytest.raises(ValueError):
            llm.parse_json(bad)


def test_edit_input_pads_wide_photo_to_white_square(tmp_path):
    out = Image.open(io.BytesIO(llm.edit_input(save(tmp_path, Image.new("RGB", (400, 200), "red")))))
    assert out.size == (1024, 1024)
    assert all(c > 240 for c in out.getpixel((512, 10)))  # padding band is white
    r, g, b = out.getpixel((512, 512))
    assert r > 200 and g < 40  # product stays red, not stretched away


def test_transparent_png_flattens_to_white(tmp_path):
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (25, 25, 75, 75))
    out = Image.open(io.BytesIO(llm.model_jpeg(save(tmp_path, im))))
    assert all(c > 240 for c in out.getpixel((5, 5)))


def test_exif_rotation_applied(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # camera says: rotate 90° clockwise to display
    file = save(tmp_path, Image.new("RGB", (300, 100), "blue"), "r.jpg", exif=exif)
    assert Image.open(io.BytesIO(llm.model_jpeg(file))).size == (100, 300)
    assert Image.open(io.BytesIO(llm.edit_input(file))).getpixel((512, 512))[2] > 200


def test_chat_json_retries_once_then_raises(monkeypatch):
    replies = iter([{"content": "not json"}, {"content": '```json\n{"ok": true}\n```'}])
    monkeypatch.setattr(llm, "chat", lambda *a, **k: next(replies))
    assert llm.chat_json([], {}) == {"ok": True}
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"content": "nope"})
    with pytest.raises(llm.LLMError, match="두 번"):
        llm.chat_json([], {})


def test_http_error_becomes_llm_error_with_body(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"PrefillDoesNotFit"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="400 PrefillDoesNotFit"):
        llm.chat([{"role": "user", "content": "x"}])


def test_connection_refused_becomes_llm_error(monkeypatch):
    def refused(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refused)
    with pytest.raises(llm.LLMError, match="모델 서버 오류"):
        llm.chat([])


def test_chat_strips_inline_think(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"choices": [{"message": {"content": "<think>음…</think>\n좋아요"}}]})
    assert llm.chat([])["content"] == "좋아요"


def test_chat_sends_schema_tools_and_thinking(monkeypatch):
    sent = {}

    def fake_post(route, body, timeout=300):
        sent.update(body, route=route)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    llm.chat([], tools=[{"type": "function"}], schema={"type": "object"}, thinking=True)
    assert sent["route"] == "/v1/chat/completions"
    assert sent["model"] == llm.TEXT_MODEL
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    assert sent["tool_choice"] == "auto"
    assert sent["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_edit_image_request_shape(monkeypatch, tmp_path):
    sent = {}

    def fake_post(route, body, timeout=300):
        sent.update(body, route=route)
        return {"data": [{"b64_json": "UE5H"}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm.edit_image(save(tmp_path, Image.new("RGB", (50, 50))), "대리석 위") == b"PNG"
    assert sent["route"] == "/v1/images/generations"
    assert {k: sent[k] for k in ("model", "mode", "size", "steps", "strength", "response_format")} == {
        "model": llm.IMAGE_MODEL, "mode": "edit", "size": "1024x1024", "steps": llm.IMAGE_STEPS,
        "strength": 0.3, "response_format": "b64_json"}
    assert sent["prompt"].startswith("대리석 위") and not sent["image"].startswith("data:")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_llm.py -q`
Expected: FAIL — `ImportError: cannot import name 'llm'`

- [ ] **Step 3: `pagegen/llm.py` 구현**

```python
"""MLX Serve client (spec §2, §6): chat with tools / vision / json_schema, and Qwen-Image edit. stdlib HTTP."""
import base64
import io
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

MLX_URL = os.environ.get("MLX_URL", "http://127.0.0.1:11234")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "empero-ai/Qwen3.8-9B-Distill-MLX-4bit")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")
IMAGE_STEPS = int(os.environ.get("IMAGE_STEPS", "20"))
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
log = logging.getLogger("pagegen")


class LLMError(Exception):
    """The model server failed; the message is shown to the user."""


def _post(route: str, body: dict, timeout: float = 300) -> dict:
    request = urllib.request.Request(MLX_URL + route, json.dumps(body, ensure_ascii=False).encode(),
                                     {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        raise LLMError(f"모델 서버 오류: {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LLMError(f"모델 서버 오류: {e}") from e


def chat(messages: list[dict], *, tools: list[dict] | None = None, schema: dict | None = None,
         thinking: bool = False, max_tokens: int = 1024, temperature: float = 0.0) -> dict:
    body = {"model": TEXT_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": thinking}}
    if tools:
        body |= {"tools": tools, "tool_choice": "auto"}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}
    try:
        message = _post("/v1/chat/completions", body)["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"모델 응답 형식 오류: {e}") from e
    if message.get("content"):
        message["content"] = _THINK.sub("", message["content"]).strip()
    return message


def parse_json(text: str | None) -> dict:
    data = json.loads(_FENCE.sub("", (text or "").strip()))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def chat_json(messages: list[dict], schema: dict, *, max_tokens: int = 1500) -> dict:
    """json_schema call, thinking off; one retry at temperature 0.3 (spec §4.1, §5)."""
    error: Exception | None = None
    for temperature in (0.0, 0.3):
        try:
            return parse_json(chat(messages, schema=schema, max_tokens=max_tokens, temperature=temperature).get("content"))
        except (LLMError, ValueError) as e:
            log.warning("JSON retry: %s", e)
            error = e
    raise LLMError(f"모델 응답을 두 번 받지 못했습니다: {error}")


def _upright_rgb(file: Path) -> Image.Image:
    """EXIF rotation applied; transparency flattened onto white (cut-out PNGs would turn black)."""
    with Image.open(file) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            canvas = Image.new("RGB", im.size, "white")
            canvas.paste(im, mask=im.getchannel("A"))
            return canvas
        return im.convert("RGB")


def _jpeg(im: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    im.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def model_jpeg(file: Path, max_side: int = 1280) -> bytes:
    im = _upright_rgb(file)
    im.thumbnail((max_side, max_side))
    return _jpeg(im, 90)


def edit_input(file: Path, size: int = 1024) -> bytes:
    """Aspect-preserving contain on a white square, so the server never has to crop or stretch (spec §6)."""
    im = ImageOps.contain(_upright_rgb(file), (size, size))
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    return _jpeg(canvas, 95)


def image_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def edit_image(file: Path, instruction: str) -> bytes:
    body = {"model": IMAGE_MODEL, "prompt": f"{instruction}. 제품의 모양, 색상, 로고와 무늬는 그대로 보존한다.",
            "mode": "edit", "image": base64.b64encode(edit_input(file)).decode(), "size": "1024x1024",
            "steps": IMAGE_STEPS, "strength": 0.3, "response_format": "b64_json"}
    try:
        return base64.b64decode(_post("/v1/images/generations", body, timeout=600)["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMError(f"이미지 응답 형식 오류: {e}") from e
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_llm.py -q`
Expected: `10 passed`

- [ ] **Step 5: 실서버 연결 한 번 확인** (MLX Serve가 :11234에서 실행 중일 때)

Run:
```bash
uv run python -c "from pagegen import llm; print(llm.chat_json([{'role':'user','content':'{\"a\": 1}을 그대로 JSON으로'}], {'type':'object','properties':{'a':{'type':'integer'}},'required':['a'],'additionalProperties':False}))"
```
Expected: `{'a': 1}`

- [ ] **Step 6: 커밋**

```bash
git add pagegen/llm.py tests/test_llm.py
git commit -m "feat: MLX Serve client with json_schema retry and photo prep"
```

---

### Task 4: `prompts.py`와 채팅 도구 루프 (Claude)

**Files:**
- Create: `pagegen/prompts.py`, `pagegen/agent.py`, `tests/test_agent.py`

**Interfaces:**
- Consumes: `pagegen.page` 전체(Task 1), `pagegen.llm.LLMError`·`chat`·`chat_json`·`edit_image`(Task 3)
- Produces:
  - `pagegen.prompts`: `TOOLS: list[dict]`(OpenAI 도구 7개), `chat_system(page: dict) -> str`, `JUDGE_SYSTEM: str`, `JUDGE_SCHEMA: dict`, `ANALYZE_SYSTEM`, `ANALYZE_SCHEMA`, `COPY_SYSTEM`, `COPY_SCHEMA`
  - `pagegen.agent`:
    - `chat_turn(folder: Path, page: dict, chat: list[dict], text: str, llm=pagegen.llm) -> tuple[dict, bool]` — page·chat을 제자리에서 바꾸고 `(assistant 메시지, 페이지 변경 여부)`를 돌려준다. 텍스트 모델 실패는 `LLMError`로 올린다
    - `judge(llm, text: str, basis: str) -> list[str]` — 근거 없는 구절 목록, 두 번 실패하면 `LLMError`
    - `basis_of(page, chat) -> str`, `edit_photo(folder, page, section_id, instruction, llm) -> str`
  - assistant 메시지 모양: `{"role": "assistant", "text": str, "tools": [{"name", "args", "ok", "result"}]}`
  - `llm` 인자는 `chat`, `chat_json`, `edit_image`, `model_jpeg`, `image_part`를 가진 객체(모듈 또는 테스트용 가짜)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_agent.py`:

```python
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

    def __init__(self, chats=(), jsons=(), edit=b"\x89PNG fake"):
        self.chats, self.jsons, self.edit = list(chats), list(jsons), edit
        self.chat_calls, self.json_calls = [], []

    def chat(self, messages, **kw):
        self.chat_calls.append(json.loads(json.dumps(messages)))
        return self.chats.pop(0)

    def chat_json(self, messages, schema, **kw):
        self.json_calls.append(messages)
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_agent.py -q`
Expected: FAIL — `ImportError: cannot import name 'agent'`

- [ ] **Step 3: `pagegen/prompts.py` 작성**

판정 프롬프트는 계획 단계 실측(2026-10-16, base 9B, thinking 끔)에서 8건 중 7건을 맞힌 "구절 분류" 방식이다. Task 9에서 Codex가 문구를 다듬는다.

```python
"""Prompts, JSON schemas and tool definitions. Task 9 tunes the wording against the real model (scripts/eval_tools.py)."""
import json

from .page import KINDS

CHAT_RULES = """너는 상세페이지 편집 에이전트다. 사용자의 요청을 도구로 처리하고, 끝나면 무엇을 바꿨는지 한두 문장의 한국어로 답한다.
규칙:
1. "짧게", "감성적으로", "강하게"처럼 문구를 다듬는 요청은 되묻지 말고 바로 update_text로 고친다. 섹션을 말하지 않으면 hero 섹션을 고친다.
2. 감사·인사·잡담에는 도구를 부르지 않고 한 문장으로 답한다.
3. 사용자가 말하지 않은 제품 사실(기능, 성능, 소재, 크기, 인증, 호환)은 새로 쓰지 않는다. 사용자가 대화에서 직접 말한 사실은 그대로 쓴다.
4. ask_user는 꼭 필요한 사실이 페이지와 대화 어디에도 없을 때만 쓴다. 문구 다듬기에는 쓰지 않는다.
5. 사진의 배경·장면을 바꾸는 요청은 edit_photo, 원래 사진으로 되돌리는 요청은 use_photo를 쓴다.
6. 도구 결과가 '오류:'로 시작하면 그 내용대로 고쳐 다시 시도하거나, 할 수 없는 이유를 사용자에게 알린다.
섹션 종류: hero(대표 사진+헤드라인), points(특징, 본문 한 줄에 '제목: 설명'), scene(연출 사진+설명), spec_table(제품 정보, 한 줄에 '항목: 값'), notice(관리·주의).
현재 페이지(JSON):
"""


def chat_system(page: dict) -> str:
    photos = [{k: p[k] for k in ("id", "origin", "source") if k in p} for p in page["photos"]]
    view = {"product": page["product"], "photos": photos, "sections": page["sections"]}
    return CHAT_RULES + json.dumps(view, ensure_ascii=False, separators=(",", ":"))


def _tool(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(properties)}}}


_S = {"type": "string"}
TOOLS = [
    _tool("update_text", "섹션의 헤드라인이나 본문을 새 문구로 바꾼다. 문구 다듬기 요청은 되묻지 말고 이 도구로 바로 고친다.",
          {"section_id": _S, "field": {"type": "string", "enum": ["headline", "body"]}, "text": _S}),
    _tool("edit_photo", "섹션 사진의 업로드 원본을 편집해 배경·장면을 바꾼 연출 컷으로 교체한다. 1분 정도 걸린다.",
          {"section_id": _S, "instruction": {"type": "string", "description": "원하는 배경이나 장면, 한 문장"}}),
    _tool("use_photo", "섹션 사진을 이미 있는 사진(업로드 원본 또는 이전 편집본)으로 바꾼다. '원래 사진으로'는 업로드 원본 id를 쓴다.",
          {"section_id": _S, "photo_id": _S}),
    _tool("add_section", "after_section_id 다음에 새 섹션을 넣는다(빈 문자열이면 맨 앞). points·spec_table 본문은 한 줄에 '제목: 설명'.",
          {"kind": {"type": "string", "enum": list(KINDS)}, "after_section_id": _S, "headline": _S, "body": _S}),
    _tool("remove_section", "섹션을 지운다.", {"section_id": _S}),
    _tool("move_section", "섹션을 after_section_id 다음으로 옮긴다(빈 문자열이면 맨 앞).",
          {"section_id": _S, "after_section_id": _S}),
    _tool("ask_user", "꼭 필요한 제품 사실(무게, 소재 등)이 페이지와 대화 어디에도 없을 때만 사용자에게 묻는다.",
          {"question": _S}),
]

JUDGE_SYSTEM = """새 문구를 짧은 구절로 나누고 각 구절을 분류해 JSON으로 답하라. "A하고 B"처럼 이어진 주장은 A와 B를 따로 나눈다.
type: "fact" = 제품의 기능·성능(보온·보냉·방수·내구성 등)·소재·크기·인증·호환·사용 가능 여부를 단정하는 구절. "mood" = 분위기·감정·장면·추천·꾸밈말.
supported: fact일 때만 따진다. 근거 텍스트나 사진에서 보이는 것에 같은 뜻이 있으면 true, 근거에 없는 성능·기능·소재면 false. mood는 항상 true.
예) 근거 '이중 진공 단열' → "따뜻함이 오래가요"는 fact/true(단열의 결과). "차가운 음료도 시원하게"는 fact/false(보냉은 근거에 없음)."""

JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["phrases"], "properties": {
    "phrases": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                           "required": ["phrase", "type", "supported"], "properties": {
            "phrase": {"type": "string"}, "type": {"type": "string", "enum": ["fact", "mood"]},
            "supported": {"type": "boolean"}}}}}}

ANALYZE_SYSTEM = """너는 상품 사진 분석가다. 사진과 판매자 입력을 보고 JSON으로 답한다.
- category: 상품 분류 한 단어(예: 주방용품, 의류, 식품, 가전).
- visible_features: 사진에서 실제로 보이는 특징만, 최대 5개(예: "무광 표면", "원터치 뚜껑"). 추측하지 않는다.
- colors: 사진에서 보이는 색, 최대 5개.
- facts: 판매자 설명에 적힌 사실만 {key, value}로. value는 설명의 표현을 그대로 옮긴다(예: {"key": "용량", "value": "500ml"}). 설명에 없으면 빈 목록.
- questions: 상세페이지에 필요한데 설명에 없는 정보를 묻는 질문, 최대 3개(예: "용량은 얼마인가요?")."""

_STRINGS = {"type": "array", "items": {"type": "string"}}
ANALYZE_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["category", "visible_features", "colors", "facts", "questions"], "properties": {
    "category": {"type": "string"}, "visible_features": _STRINGS, "colors": _STRINGS, "questions": _STRINGS,
    "facts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "value"],
                                         "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}}}}

COPY_SYSTEM = """너는 한국 온라인 쇼핑몰 상세페이지 카피라이터다. 상품명·판매자 설명·사진 분석만 보고 JSON으로 답한다.
- 헤드라인은 20자 이내, 본문은 한두 문장.
- 기능·성능·소재·크기·인증 같은 제품 사실은 판매자 설명과 사진 분석에 있는 것만 쓴다. 숫자는 판매자 설명에 있는 것만 쓴다.
- 없는 사실 대신 분위기·사용 장면·감성 표현으로 채운다.
- points.items는 3개, 각 title은 10자 이내.
- notice는 판매자 설명에 관리·주의 사항이 있을 때만 쓰고, 없으면 headline과 body를 빈 문자열로 둔다.
- scene.photo_instruction은 이 제품이 잘 어울리는 배경이나 사용 장면을 한 문장으로 쓴다(예: "아침 햇살이 드는 원목 식탁 위")."""


def _object(**properties) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


_T = {"type": "string"}
COPY_SCHEMA = _object(
    hero=_object(headline=_T, body=_T),
    points=_object(headline=_T, items={"type": "array", "items": _object(title=_T, text=_T)}),
    scene=_object(headline=_T, body=_T, photo_instruction=_T),
    notice=_object(headline=_T, body=_T),
)
```

- [ ] **Step 4: `pagegen/agent.py` 구현 (채팅 부분)**

```python
"""Chat turns (tool calls, spec §4.2) and the first draft (fixed order, spec §4.1). `llm` is injectable for tests."""
import json
from collections.abc import Callable
from pathlib import Path

from . import llm as default_llm
from . import page as pg
from . import prompts
from .llm import LLMError

MAX_ROUNDS = 4
HISTORY = 12  # the last 6 turns, user + assistant
MUTATING = {"update_text", "edit_photo", "use_photo", "add_section", "remove_section", "move_section"}


def basis_of(page: dict, chat: list[dict]) -> str:
    seen = page["analysis"]["visible_features"] + page["analysis"]["colors"]
    return f"근거 텍스트: {pg.corpus_of(page, chat)}\n사진에서 보이는 것: {', '.join(seen) or '(없음)'}"


def judge(llm, text: str, basis: str) -> list[str]:
    """Phrases of `text` asserting product facts missing from `basis` (spec §5). Raises LLMError: fail-closed."""
    if not text.strip():
        return []
    data = llm.chat_json([{"role": "system", "content": prompts.JUDGE_SYSTEM},
                          {"role": "user", "content": f"{basis}\n새 문구: {text}"}],
                         prompts.JUDGE_SCHEMA, max_tokens=400)
    return [str(p.get("phrase") or text) for p in data.get("phrases", [])
            if p.get("type") != "mood" and p.get("supported") is not True]


def _verifier(llm, basis: str) -> Callable[[str], None]:
    def verify(text: str) -> None:
        try:
            flagged = judge(llm, text, basis)
        except LLMError:
            raise pg.ToolError("오류: 주장 검사를 하지 못해 반영하지 않았습니다.") from None
        if flagged:
            raise pg.ToolError(f"오류: 근거 없는 주장 '{', '.join(flagged)}'. 빼거나 ask_user로 물어보세요.")
    return verify


def edit_photo(folder: Path, page: dict, section_id: str, instruction: str, llm) -> str:
    source = pg.edit_source(page, section_id)
    try:
        png = llm.edit_image(folder / source["file"], instruction)
    except LLMError as e:
        raise pg.ToolError(f"오류: 사진 편집에 실패해 이전 사진을 유지합니다. ({e})") from None
    photo = pg.add_edit(page, source["id"], instruction)
    (folder / photo["file"]).write_bytes(png)
    pg.find_section(page, section_id)["photo"] = photo["id"]
    return f"{section_id} 사진을 편집본 {photo['id']}로 교체"


def _args(call: dict) -> dict:
    raw = call.get("function", {}).get("arguments")
    try:
        args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (ValueError, TypeError):
        raise pg.ToolError("오류: 인자 JSON을 읽을 수 없습니다. 올바른 JSON 객체로 다시 호출하세요.") from None
    if not isinstance(args, dict):
        raise pg.ToolError("오류: 인자는 JSON 객체여야 합니다.")
    return args


def _run_tool(folder: Path, page: dict, name: str, args: dict, corpus: str, verify, llm) -> str:
    a = {key: str(value) for key, value in args.items()}
    try:
        match name:
            case "update_text":
                return pg.update_text(page, a["section_id"], a["field"], a["text"], corpus, verify)
            case "add_section":
                return pg.add_section(page, a["kind"], a.get("after_section_id", ""), a.get("headline", ""),
                                      a.get("body", ""), corpus, verify)
            case "remove_section":
                return pg.remove_section(page, a["section_id"])
            case "move_section":
                return pg.move_section(page, a["section_id"], a.get("after_section_id", ""))
            case "use_photo":
                return pg.use_photo(page, a["section_id"], a["photo_id"])
            case "edit_photo":
                return edit_photo(folder, page, a["section_id"], a["instruction"], llm)
    except KeyError as e:
        raise pg.ToolError(f"오류: 인자 {e.args[0]}가 빠졌습니다.") from None
    raise pg.ToolError(f"오류: 없는 도구 {name}")


def chat_turn(folder: Path, page: dict, chat: list[dict], text: str, llm=default_llm) -> tuple[dict, bool]:
    """One user message (spec §4.2). Mutates page and chat; returns (assistant message, page changed).
    A text-model failure raises LLMError; the caller then leaves page.json as it was."""
    history = [{"role": m["role"], "content": m["text"]} for m in chat[-HISTORY:]]
    chat.append({"role": "user", "text": text})
    corpus = pg.corpus_of(page, chat)
    verify = _verifier(llm, basis_of(page, chat))
    messages = [{"role": "system", "content": prompts.chat_system(page)}, *history, {"role": "user", "content": text}]
    log: list[dict] = []
    changed, reply = False, None
    for _ in range(MAX_ROUNDS):
        message = llm.chat(messages, tools=prompts.TOOLS, thinking=True, max_tokens=1024)
        calls = message.get("tool_calls") or []
        if not calls:
            reply = message.get("content") or ("반영했어요." if changed else "무엇을 바꿀까요?")
            break
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            name, args = call.get("function", {}).get("name", ""), {}
            try:
                args = _args(call)
                if name == "ask_user":
                    reply = args.get("question") or "조금 더 알려 주세요."
                    log.append({"name": name, "args": args, "ok": True, "result": "질문"})
                    break
                result, ok = _run_tool(folder, page, name, args, corpus, verify, llm), True
            except pg.ToolError as e:
                result, ok = str(e), False
            log.append({"name": name, "args": args, "ok": ok, "result": result})
            changed |= ok and name in MUTATING
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        if reply is not None:
            break
    else:
        reply = "여기까지 반영했어요."
    answer = {"role": "assistant", "text": reply, "tools": log}
    chat.append(answer)
    return answer, changed
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_agent.py -q`
Expected: `16 passed`

- [ ] **Step 6: 커밋**

```bash
git add pagegen/prompts.py pagegen/agent.py tests/test_agent.py
git commit -m "feat: chat tool loop with claim check and photo edits"
```

---

### Task 5: 첫 초안 파이프라인 (Claude)

**Files:**
- Modify: `pagegen/agent.py` (파일 끝에 추가)
- Modify: `tests/test_agent.py` (파일 끝에 추가)

**Interfaces:**
- Consumes: Task 4의 `judge`, `basis_of`, `edit_photo`, `prompts.ANALYZE_*`, `prompts.COPY_*`
- Produces (`pagegen.agent`):
  - `make_draft(folder: Path, pid: str, product: dict, photos: list[dict], llm=pagegen.llm) -> tuple[dict, dict]` — `(page, 첫 assistant 메시지)`. 분석·문구 JSON 두 번 실패나 주장 검사 두 번 실패는 `LLMError`(초안 없음)
  - `normalize_analysis(raw: dict, corpus: str) -> dict`, `build_sections(copy: dict, analysis: dict, name: str) -> tuple[list[dict], str]`

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_agent.py` 끝에 추가)

```python
ANALYSIS = {"category": "주방용품", "visible_features": ["원터치 뚜껑"], "colors": ["실버"],
            "facts": [{"key": "용량", "value": "500ml"}, {"key": "무게", "value": "350g"}],
            "questions": ["무게는 얼마인가요?"]}
COPY = {"hero": {"headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러"},
        "points": {"headline": "이래서 좋아요", "items": [
            {"title": "넉넉한 용량", "text": "500ml"}, {"title": "가벼움", "text": "350g 초경량"},
            {"title": "보냉", "text": "차갑게도 유지"}]},
        "scene": {"headline": "어디서나", "body": "", "photo_instruction": "원목 식탁 위"},
        "notice": {"headline": "", "body": ""}}
PRODUCT = {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종"}


def draft(folder, jsons, edit=b"png"):
    fake = FakeLLM(jsons=jsons, edit=edit)
    photos = [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"}]
    page, message = agent.make_draft(folder, "20261016-153012-ab12", dict(PRODUCT), photos, fake)
    return page, message, fake


def test_draft_assembles_cleans_and_edits_scene(folder):
    # judge calls run per section in order: hero, points, scene (empty notice is dropped before judging)
    page, message, fake = draft(folder, [ANALYSIS, COPY, OK, flag("차갑게도 유지"), OK])
    assert [(s["id"], s["kind"]) for s in page["sections"]] == [
        ("s1", "hero"), ("s2", "points"), ("s3", "scene"), ("s4", "spec_table")]
    assert page["sections"][1]["body"] == "넉넉한 용량: 500ml"  # 350g had no source, 보냉 was flagged
    assert page["sections"][3]["body"] == "용량: 500ml"  # the 350g fact was not in the notes
    assert page["analysis"]["facts"] == [{"key": "용량", "value": "500ml"}]
    assert page["sections"][0]["photo"] == "p1"
    assert page["sections"][2]["photo"] == "p1-e1" and (folder / "photos/p1-e1.png").exists()
    assert page["version"] == 1 and page["product"] == PRODUCT
    assert "무게는 얼마인가요?" in message["text"] and message["role"] == "assistant"
    assert fake.json_calls[0][1]["content"][0]["type"] == "image_url"
    assert len(fake.json_calls) == 5


def test_draft_flagged_headline_becomes_product_name(folder):
    page, _, _ = draft(folder, [ANALYSIS, COPY, flag("하루 종일 따뜻하게"), OK, OK])
    assert page["sections"][0]["headline"] == "스테인리스 텀블러"


def test_draft_unlocatable_phrase_drops_the_body(folder):
    page, _, _ = draft(folder, [ANALYSIS, COPY, OK, flag("보냉 기능"), OK])
    assert [s["kind"] for s in page["sections"]] == ["hero", "scene", "spec_table"]


def test_draft_judge_failure_means_no_draft(folder):
    with pytest.raises(LLMError):
        draft(folder, [ANALYSIS, COPY, LLMError("down")])


def test_draft_scene_edit_failure_keeps_original(folder):
    page, _, _ = draft(folder, [ANALYSIS, COPY, OK, OK, OK], edit=LLMError("timeout"))
    scene = next(s for s in page["sections"] if s["kind"] == "scene")
    assert scene["photo"] == "p1" and len(page["photos"]) == 1


def test_draft_broken_analysis_raises(folder):
    with pytest.raises(LLMError):
        draft(folder, [LLMError("JSON 두 번 실패")])


def test_normalize_analysis_tolerates_junk():
    raw = {"category": None, "visible_features": ["a", "", "b"], "colors": "red",
           "facts": [{"key": "용량", "value": "500ml"}, "x", {"key": "", "value": "1"}],
           "questions": ["q1", "q2", "q3", "q4"]}
    assert agent.normalize_analysis(raw, "500ml") == {
        "category": "", "visible_features": ["a", "b"], "colors": [],
        "facts": [{"key": "용량", "value": "500ml"}], "questions": ["q1", "q2", "q3"]}
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_agent.py -q`
Expected: FAIL — `AttributeError: module 'pagegen.agent' has no attribute 'make_draft'`

- [ ] **Step 3: `pagegen/agent.py` 끝에 초안 코드 추가**

```python
def normalize_analysis(raw: dict, corpus: str) -> dict:
    """Spec §3: every key present; facts only when the value appears in the seller's text."""
    def strings(key: str, limit: int) -> list[str]:
        items = raw.get(key)
        return [str(x).strip() for x in items if str(x).strip()][:limit] if isinstance(items, list) else []

    facts = []
    for fact in raw.get("facts") or []:
        if isinstance(fact, dict):
            key, value = str(fact.get("key") or "").strip(), str(fact.get("value") or "").strip()
            if key and value and pg.is_sourced(value, corpus):
                facts.append({"key": key, "value": value})
    return {"category": str(raw.get("category") or "").strip(), "visible_features": strings("visible_features", 5),
            "colors": strings("colors", 5), "facts": facts, "questions": strings("questions", 3)}


def build_sections(copy: dict, analysis: dict, name: str) -> tuple[list[dict], str]:
    """Spec §4.1-4. Returns (sections without ids, scene photo instruction)."""
    def get(block: str, key: str) -> str:
        part = copy.get(block)
        return str(part.get(key) or "").strip() if isinstance(part, dict) else ""

    points = copy.get("points") if isinstance(copy.get("points"), dict) else {}
    items = [i for i in points.get("items") or [] if isinstance(i, dict) and str(i.get("text") or "").strip()][:3]
    sections = [
        {"kind": "hero", "headline": get("hero", "headline") or name, "body": get("hero", "body"), "photo": "p1"},
        {"kind": "points", "headline": get("points", "headline") or "이래서 좋아요",
         "body": "\n".join(f"{str(i.get('title') or '').strip()}: {str(i['text']).strip()}" for i in items)},
        {"kind": "scene", "headline": get("scene", "headline"), "body": get("scene", "body"), "photo": "p1"},
        {"kind": "notice", "headline": get("notice", "headline") or "안내", "body": get("notice", "body")},
    ]
    if analysis["facts"]:
        rows = "\n".join(f"{f['key']}: {f['value']}" for f in analysis["facts"])
        sections.insert(3, {"kind": "spec_table", "headline": "제품 정보", "body": rows})
    return sections, get("scene", "photo_instruction") or "제품이 잘 어울리는 밝은 실내 공간"


def _clean(section: dict, corpus: str, basis: str, name: str, llm) -> None:
    """Spec §4.1-6 for one model-written section: drop or replace whatever fails the number or claim check."""
    if pg.unsupported_numbers(section["headline"], corpus):
        section["headline"] = name
    lines = [line for line in section["body"].splitlines() if line.strip() and not pg.unsupported_numbers(line, corpus)]
    headline = section["headline"]
    flagged = judge(llm, "\n".join([headline, *lines]), basis)
    located = all(f in headline or any(f in line for line in lines) for f in flagged)
    if any(f in headline for f in flagged):
        section["headline"] = name
    kept = [line for line in lines if not any(f in line for f in flagged)]
    section["body"] = "\n".join(kept) if located else ""  # a phrase the judge reworded can't be located: drop it all


def _has_content(section: dict) -> bool:
    return bool(section["body"]) or section["kind"] in pg.PHOTO_KINDS


def make_draft(folder: Path, pid: str, product: dict, photos: list[dict], llm=default_llm) -> tuple[dict, dict]:
    """Spec §4.1. Returns (page, first assistant message). Raises LLMError and then no draft exists."""
    page = {"id": pid, "version": 1, "product": product, "analysis": {}, "photos": photos, "sections": []}
    corpus = pg.corpus_of(page, [])
    seller = f"상품명: {product['name']}\n판매자 설명: {product['notes'] or '(없음)'}"
    images = [llm.image_part(llm.model_jpeg(folder / p["file"])) for p in photos[:4]]
    raw = llm.chat_json([{"role": "system", "content": prompts.ANALYZE_SYSTEM},
                         {"role": "user", "content": [*images, {"type": "text", "text": seller}]}],
                        prompts.ANALYZE_SCHEMA)
    page["analysis"] = normalize_analysis(raw, corpus)
    copy = llm.chat_json([{"role": "system", "content": prompts.COPY_SYSTEM},
                          {"role": "user", "content": f"{seller}\n사진 분석: {json.dumps(page['analysis'], ensure_ascii=False)}"}],
                         prompts.COPY_SCHEMA)
    sections, instruction = build_sections(copy, page["analysis"], product["name"])
    sections = [s for s in sections if _has_content(s)]
    basis = basis_of(page, [])
    for section in sections:
        if section["kind"] != "spec_table":  # built from facts that already passed the spec check
            _clean(section, corpus, basis, product["name"], llm)
    page["sections"] = [{"id": f"s{i}", **s} for i, s in enumerate((s for s in sections if _has_content(s)), 1)]
    scene = next((s for s in page["sections"] if s["kind"] == "scene"), None)
    if scene:
        try:
            edit_photo(folder, page, scene["id"], instruction, llm)
        except pg.ToolError:
            pass  # keep the original photo (spec §4.1-5)
    text = "초안을 만들었어요. 오른쪽 미리보기를 보고 고칠 점을 말씀해 주세요."
    if page["analysis"]["questions"]:
        text += "\n더 채우려면 알려 주세요:\n" + "\n".join(f"- {q}" for q in page["analysis"]["questions"])
    return page, {"role": "assistant", "text": text, "tools": []}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q`
Expected: 전체 통과 (`test_agent.py` 23개 포함)

- [ ] **Step 5: 커밋**

```bash
git add pagegen/agent.py tests/test_agent.py
git commit -m "feat: fixed-order first draft with number and claim checks"
```

---

### Task 6: `render.py` — HTML과 내보내기 (Codex)

**Files:**
- Create: `pagegen/render.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `pagegen.page.split_line`, `pagegen.page.KINDS`(Task 1), `page.json` 모양(스펙 §3), 픽스처 `page`(`tests/conftest.py`)
- Produces (`pagegen.render`):
  - `render_html(page: dict, photo_url: Callable[[str], str]) -> str` — `photo_url`은 `photos[].file`(예: `"photos/p1.jpg"`)을 받아 `<img src>`에 넣을 URL을 돌려준다
  - `export(page_url: str, out_dir: Path) -> dict` — `{"png": Path, "jpg": Path, "sections": [Path, …]}`. `out_dir`을 비우고 다시 만든다. 섹션 파일 이름은 `NN-<kind>.png`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_render.py`:

```python
import pytest
from PIL import Image

from pagegen import render


def html_of(page):
    return render.render_html(page, lambda file: f"/files/{file}")


def test_sections_carry_ids_and_kinds_in_order(page):
    html = html_of(page)
    for s in page["sections"]:
        assert f'<section data-section-id="{s["id"]}" data-kind="{s["kind"]}"' in html
    assert html.index('"s1"') < html.index('"s2"') < html.index('"s3"')


def test_model_text_is_escaped(page):
    page["sections"][0]["headline"] = "<script>alert(1)</script>"
    page["product"]["name"] = "<b>이름</b>"
    html = html_of(page)
    assert "<script>" not in html and "&lt;script&gt;" in html and "<b>" not in html


def test_points_and_spec_table_split_lines(page):
    page["sections"][1]["body"] = "넉넉한 용량: 500ml\n원터치:한 손으로"
    page["sections"].append({"id": "s4", "kind": "spec_table", "headline": "제품 정보",
                             "body": "용량: 500ml\n운영 시간: 10:30"})
    html = html_of(page)
    assert "<strong>원터치</strong>" in html and "한 손으로" in html
    assert "<th>운영 시간</th><td>10:30</td>" in html


def test_photo_uses_photo_url_and_skips_unknown(page):
    assert 'src="/files/photos/p1-e1.png"' in html_of(page)
    page["sections"][0]["photo"] = "p9"
    assert "<img" not in html_of(page)


def test_no_external_resources_and_fixed_width(page):
    html = html_of(page)
    assert "http://" not in html and "https://" not in html and "@import" not in html
    assert "860px" in html


def test_export_png_jpg_and_sections(page, tmp_path):
    pytest.importorskip("playwright")
    photo = tmp_path / "p.png"
    Image.new("RGB", (860, 600), "orange").save(photo)
    html_file = tmp_path / "page.html"
    html_file.write_text(render.render_html(page, lambda file: photo.as_uri()), "utf-8")
    try:
        files = render.export(html_file.as_uri(), tmp_path / "export")
    except Exception as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("uv run playwright install chromium 필요")
        raise
    with Image.open(files["png"]) as im:
        assert im.width == 860 and im.height > 600
    assert files["jpg"].exists()
    assert [f.name for f in files["sections"]] == ["01-hero.png", "02-points.png", "03-notice.png"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'render'`

- [ ] **Step 3: `pagegen/render.py` 구현**

아래는 동작하는 기본 디자인이다. 테스트를 지키는 한 CSS는 더 다듬어도 된다(일반 쇼핑몰 상세페이지 톤, 860px, 시스템 폰트, 외부 리소스 없음).

```python
"""page.json → HTML (preview and capture share it, spec §8) and Playwright export."""
import shutil
from collections.abc import Callable
from html import escape
from pathlib import Path

from .page import KINDS, split_line

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:#1d1d1f;font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;word-break:keep-all}
main{width:860px;margin:0 auto}
section{padding:72px 64px;text-align:center}
section img{display:block;width:100%;height:auto}
h1{font-size:44px;line-height:1.3}
h2{font-size:32px;line-height:1.35;margin-bottom:28px}
section[data-kind=hero],section[data-kind=scene]{padding:0 0 72px}
section[data-kind=hero] h1{margin:56px 64px 16px}
section[data-kind=scene] h2{margin:48px 64px 12px}
section[data-kind=hero] p,section[data-kind=scene] p{font-size:20px;color:#555;line-height:1.6;margin:0 64px}
section[data-kind=points]{background:#f5f5f7}
section[data-kind=points] ul{list-style:none;display:grid;gap:16px;text-align:left}
section[data-kind=points] li{background:#fff;border-radius:16px;padding:28px 32px}
section[data-kind=points] strong{display:block;font-size:22px;margin-bottom:8px}
section[data-kind=points] span{font-size:18px;color:#555;line-height:1.6}
section[data-kind=spec_table] table{width:100%;border-collapse:collapse;text-align:left;font-size:18px}
section[data-kind=spec_table] th,section[data-kind=spec_table] td{padding:18px 8px;border-bottom:1px solid #e5e5ea}
section[data-kind=spec_table] th{width:30%;color:#6e6e73;font-weight:500}
section[data-kind=notice]{background:#f5f5f7;text-align:left}
section[data-kind=notice] p{font-size:17px;color:#555;line-height:1.7}
"""


def _lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.strip()]


def _section(section: dict, photos: dict[str, str], photo_url: Callable[[str], str]) -> str:
    kind = section["kind"] if section["kind"] in KINDS else "notice"
    headline, body = escape(section["headline"]), section["body"]
    image = ""
    if kind in ("hero", "scene") and section.get("photo") in photos:
        image = f'<img src="{escape(photo_url(photos[section["photo"]]))}" alt="">'
    if kind == "hero":
        inner = f"{image}<h1>{headline}</h1><p>{escape(body)}</p>"
    elif kind == "scene":
        inner = f"{image}<h2>{headline}</h2><p>{escape(body)}</p>"
    elif kind == "points":
        items = "".join(f"<li><strong>{escape(k)}</strong><span>{escape(v)}</span></li>"
                        for k, v in map(split_line, _lines(body)))
        inner = f"<h2>{headline}</h2><ul>{items}</ul>"
    elif kind == "spec_table":
        rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in map(split_line, _lines(body)))
        inner = f"<h2>{headline}</h2><table>{rows}</table>"
    else:
        inner = f"<h2>{headline}</h2>" + "".join(f"<p>{escape(line)}</p>" for line in _lines(body))
    return f'<section data-section-id="{escape(section["id"])}" data-kind="{kind}">{inner}</section>'


def render_html(page: dict, photo_url: Callable[[str], str]) -> str:
    photos = {p["id"]: p["file"] for p in page["photos"]}
    body = "".join(_section(s, photos, photo_url) for s in page["sections"])
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<title>{escape(page["product"]["name"])}</title><style>{CSS}</style></head>'
            f"<body><main>{body}</main></body></html>")


def export(page_url: str, out_dir: Path) -> dict:
    """Full-page PNG + JPG (quality 90) + one PNG per <section>, all 860px wide (spec §8)."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    shutil.rmtree(out_dir, ignore_errors=True)
    (out_dir / "sections").mkdir(parents=True)
    png, jpg, sections = out_dir / "page.png", out_dir / "page.jpg", []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            tab = browser.new_page(viewport={"width": 860, "height": 1200}, device_scale_factor=1)
            tab.goto(page_url, wait_until="networkidle")
            tab.screenshot(path=png, full_page=True)
            for i, element in enumerate(tab.query_selector_all("section"), 1):
                file = out_dir / "sections" / f"{i:02d}-{element.get_attribute('data-kind')}.png"
                element.screenshot(path=file)
                sections.append(file)
        finally:
            browser.close()
    with Image.open(png) as im:
        im.convert("RGB").save(jpg, quality=90)
    return {"png": png, "jpg": jpg, "sections": sections}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run playwright install chromium && uv run pytest tests/test_render.py -q`
Expected: `6 passed`

- [ ] **Step 5: 보고** — 브리프 하단 `## 결과`에 변경 파일, 테스트 출력, `export`로 만든 샘플 PNG 경로(`.orchestration/reports/2026-10-16-render/`)를 적는다. Claude가 검수 후 커밋한다:

```bash
git add pagegen/render.py tests/test_render.py
git commit -m "feat: page HTML renderer and Playwright export"
```

---

### Task 7: `static/index.html` — 채팅 화면 (Codex)

**Files:**
- Create: `static/index.html`

**Interfaces:**
- Consumes: 스펙 §7 API와 응답 형식(`POST /projects` → `{id, version, messages}`, `POST /projects/{id}/chat` → `{reply, version}`, `POST /projects/{id}/undo` → `{version}`, `POST /projects/{id}/export` → `{png, jpg, sections}`, `GET /projects/{id}` → `{page, chat}`, 오류는 `{"error": "…"}`)
- Produces: `GET /`가 돌려줄 단일 HTML 파일(외부 리소스 없음)

- [ ] **Step 1: `static/index.html` 작성**

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>상세페이지 에이전트</title>
<style>
  :root { --line: #e5e5ea; --muted: #6e6e73; --accent: #0a66ff; }
  * { box-sizing: border-box; }
  body { margin: 0; height: 100vh; display: grid; grid-template-columns: 420px 1fr;
         font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; color: #1d1d1f; }
  aside { display: flex; flex-direction: column; border-right: 1px solid var(--line); min-height: 0; }
  #log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { padding: 10px 12px; border-radius: 12px; white-space: pre-wrap; line-height: 1.5; font-size: 14px; max-width: 92%; }
  .user { align-self: flex-end; background: var(--accent); color: #fff; }
  .assistant { align-self: flex-start; background: #f2f2f7; }
  .error { align-self: stretch; background: #fff1f0; color: #c00; }
  .tools { margin-top: 6px; font-size: 12px; color: var(--muted); }
  form { padding: 12px 16px; border-top: 1px solid var(--line); display: grid; gap: 8px; }
  input, textarea, button { font: inherit; font-size: 14px; }
  textarea, input[type=text] { padding: 8px; border: 1px solid var(--line); border-radius: 8px; }
  textarea { resize: vertical; min-height: 56px; }
  button { padding: 8px 12px; border: 0; border-radius: 8px; background: var(--accent); color: #fff; cursor: pointer; }
  button.secondary { background: #f2f2f7; color: #1d1d1f; }
  button:disabled, textarea:disabled, input:disabled { opacity: .5; cursor: default; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; }
  #busy { display: none; padding: 0 16px 8px; font-size: 13px; color: var(--muted); }
  body.busy #busy { display: block; }
  main { overflow: auto; background: #f2f2f7; display: flex; justify-content: center; }
  iframe { width: 860px; height: 100%; border: 0; background: #fff; }
  #empty { margin: auto; color: var(--muted); }
  #links a { display: block; font-size: 13px; }
</style>
</head>
<body>
<aside>
  <div id="log"></div>
  <div id="busy">작업 중이에요… 사진 편집이 들어가면 1분 정도 걸려요.</div>
  <form id="start">
    <input type="file" name="photos" accept="image/jpeg,image/png,image/webp" multiple required>
    <input type="text" name="name" placeholder="상품명" required>
    <textarea name="notes" placeholder="상품 설명 (용량, 소재, 관리 방법 등 아는 것을 자유롭게)"></textarea>
    <button>초안 만들기</button>
  </form>
  <form id="chat" hidden>
    <textarea name="text" placeholder="예: 헤드라인을 더 짧게 / 배경을 대리석으로" required></textarea>
    <div class="row">
      <button>보내기</button>
      <button type="button" class="secondary" id="undo">되돌리기</button>
      <button type="button" class="secondary" id="export">내보내기</button>
      <button type="button" class="secondary" id="new">새 상품</button>
    </div>
    <div id="links"></div>
  </form>
</aside>
<main>
  <p id="empty">사진과 설명을 올리면 초안이 여기에 보여요.</p>
  <iframe id="preview" title="상세페이지 미리보기" hidden></iframe>
</main>
<script>
const $ = (selector) => document.querySelector(selector);
let projectId = location.hash.slice(1);

function addMessage(m) {
  const div = document.createElement("div");
  div.className = "msg " + m.role;
  div.textContent = m.text;
  if (m.tools && m.tools.length) {
    const tools = document.createElement("div");
    tools.className = "tools";
    tools.textContent = m.tools.map((t) => (t.ok ? "✓ " : "✗ ") + t.name + " — " + t.result).join("\n");
    div.appendChild(tools);
  }
  $("#log").appendChild(div);
  $("#log").scrollTop = $("#log").scrollHeight;
}

function showError(error) { addMessage({ role: "error", text: error.message }); }

function showPreview(version) {
  $("#empty").hidden = true;
  $("#preview").hidden = false;
  $("#preview").src = `/projects/${projectId}/page?v=${version}`;
}

function setBusy(busy) {
  document.body.classList.toggle("busy", busy);
  document.querySelectorAll("button, textarea, input").forEach((el) => (el.disabled = busy));
}

async function call(url, options = {}) {
  setBusy(true);
  try {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({ error: `서버 오류 (${res.status})` }));
    if (!res.ok) throw new Error(data.error || `서버 오류 (${res.status})`);
    return data;
  } finally {
    setBusy(false);
  }
}

function openProject(id, chat, version) {
  projectId = id;
  location.hash = id;
  $("#start").hidden = true;
  $("#chat").hidden = false;
  chat.forEach(addMessage);
  showPreview(version);
}

$("#start").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const data = await call("/projects", { method: "POST", body: new FormData(ev.target) });
    openProject(data.id, data.messages, data.version);
  } catch (e) { showError(e); }
});

$("#chat").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const box = ev.target.elements.text;
  const text = box.value.trim();
  if (!text) return;
  addMessage({ role: "user", text });
  box.value = "";
  try {
    const data = await call(`/projects/${projectId}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    addMessage(data.reply);
    showPreview(data.version);
  } catch (e) { showError(e); }
});

// Enter sends, Shift+Enter breaks the line; never send while the Korean IME is still composing.
$("#chat textarea").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    $("#chat").requestSubmit();
  }
});

$("#undo").addEventListener("click", async () => {
  try {
    const data = await call(`/projects/${projectId}/undo`, { method: "POST" });
    addMessage({ role: "assistant", text: "한 단계 되돌렸어요." });
    showPreview(data.version);
  } catch (e) { showError(e); }
});

$("#export").addEventListener("click", async () => {
  try {
    const data = await call(`/projects/${projectId}/export`, { method: "POST" });
    const links = [["전체 PNG", data.png], ["전체 JPG", data.jpg], ...data.sections.map((u, i) => [`섹션 ${i + 1}`, u])];
    $("#links").replaceChildren(...links.map(([label, url]) => {
      const a = document.createElement("a");
      a.href = url;
      a.textContent = label;
      a.download = "";
      return a;
    }));
  } catch (e) { showError(e); }
});

$("#new").addEventListener("click", () => { location.hash = ""; location.reload(); });

if (projectId) {
  fetch(`/projects/${projectId}`)
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error("프로젝트를 찾을 수 없어요."))))
    .then((d) => openProject(projectId, d.chat, d.page.version))
    .catch((e) => { location.hash = ""; projectId = ""; showError(e); });
}
</script>
</body>
</html>
```

- [ ] **Step 2: 정적 확인**

Run: `grep -nE "https?://|<script src|<link " static/index.html`
Expected: 출력 없음 (외부 리소스 없음)

- [ ] **Step 3: 보고** — 실제 화면 확인은 Task 8 서버가 생긴 뒤 Task 10에서 한다. 여기서는 파일과 위 확인 결과만 보고한다. Claude가 검수 후 커밋한다:

```bash
git add static/index.html
git commit -m "feat: chat and live preview page"
```

---

### Task 8: `server.py` — FastAPI (Claude)

**Files:**
- Create: `pagegen/server.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `store`(Task 2), `agent.make_draft`·`agent.chat_turn`(Task 4·5), `render.render_html`·`render.export`(Task 6), `llm.LLMError`(Task 3), `static/index.html`(Task 7)
- Produces: `pagegen.server.app`(FastAPI), `python -m pagegen.server`로 `127.0.0.1:8000` 실행

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_server.py`:

```python
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pagegen import agent, server, store
from pagegen.llm import LLMError


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch, page):
    monkeypatch.setattr(store, "ROOT", tmp_path)

    def fake_draft(folder, pid, product, photos, llm=None):
        return dict(page, id=pid, product=product, photos=photos), {"role": "assistant", "text": "초안", "tools": []}

    monkeypatch.setattr(agent, "make_draft", fake_draft)
    return TestClient(server.app)


def create(client):
    r = client.post("/projects", data={"name": "텀블러", "notes": "500ml"},
                    files=[("photos", ("a.jpg", jpeg(), "image/jpeg"))])
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["messages"][0]["text"] == "초안"
    return r.json()["id"]


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "상세페이지 에이전트" in r.text


def test_create_get_preview_and_photo(client):
    pid = create(client)
    data = client.get(f"/projects/{pid}").json()
    assert data["page"]["product"] == {"name": "텀블러", "notes": "500ml"}
    html = client.get(f"/projects/{pid}/page")
    assert html.status_code == 200 and 'data-section-id="s1"' in html.text
    assert html.headers["cache-control"] == "no-store"
    assert client.get(f"/projects/{pid}/photos/p1.jpg").status_code == 200


@pytest.mark.parametrize("data, files", [
    ({"name": " "}, [("photos", ("a.jpg", jpeg(), "image/jpeg"))]),
    ({"name": "x"}, [("photos", ("a.txt", b"hello", "text/plain"))]),
    ({"name": "x"}, None),
])
def test_create_rejects_bad_input_with_400(client, data, files):
    r = client.post("/projects", data=data, files=files)
    assert r.status_code == 400 and r.json()["error"]


def test_chat_change_bumps_version_then_undo(client, monkeypatch):
    pid = create(client)

    def fake_turn(folder, page, chat, text, llm=None):
        chat.append({"role": "user", "text": text})
        page["sections"].pop()
        reply = {"role": "assistant", "text": "지웠어요", "tools": []}
        chat.append(reply)
        return reply, True

    monkeypatch.setattr(agent, "chat_turn", fake_turn)
    r = client.post(f"/projects/{pid}/chat", json={"text": "마지막 섹션 지워"})
    assert r.json() == {"reply": {"role": "assistant", "text": "지웠어요", "tools": []}, "version": 2}
    assert client.post(f"/projects/{pid}/undo").json() == {"version": 1}
    data = client.get(f"/projects/{pid}").json()
    assert len(data["page"]["sections"]) == 3 and len(data["chat"]) == 3  # undo keeps the conversation
    assert client.post(f"/projects/{pid}/undo").status_code == 409


def test_chat_without_change_keeps_version(client, monkeypatch):
    pid = create(client)

    def fake_turn(folder, page, chat, text, llm=None):
        chat += [{"role": "user", "text": text}, {"role": "assistant", "text": "네", "tools": []}]
        return chat[-1], False

    monkeypatch.setattr(agent, "chat_turn", fake_turn)
    assert client.post(f"/projects/{pid}/chat", json={"text": "고마워"}).json()["version"] == 1
    assert client.post(f"/projects/{pid}/undo").status_code == 409


def test_chat_model_error_keeps_page_and_user_message(client, monkeypatch):
    pid = create(client)

    def down(folder, page, chat, text, llm=None):
        chat.append({"role": "user", "text": text})
        page["sections"].clear()
        raise LLMError("모델 서버 오류: 연결 거부")

    monkeypatch.setattr(agent, "chat_turn", down)
    r = client.post(f"/projects/{pid}/chat", json={"text": "안녕"})
    assert r.status_code == 502 and "연결 거부" in r.json()["error"]
    data = client.get(f"/projects/{pid}").json()
    assert len(data["page"]["sections"]) == 3 and data["chat"][-1] == {"role": "user", "text": "안녕"}


def test_draft_model_error_is_502(client, monkeypatch):
    def down(*a, **k):
        raise LLMError("모델 서버 오류: 연결 거부")

    monkeypatch.setattr(agent, "make_draft", down)
    r = client.post("/projects", data={"name": "x"}, files=[("photos", ("a.jpg", jpeg(), "image/jpeg"))])
    assert r.status_code == 502 and "연결 거부" in r.json()["error"]


def test_empty_chat_message_is_400(client):
    pid = create(client)
    assert client.post(f"/projects/{pid}/chat", json={"text": "  "}).status_code == 400


def test_unknown_project_and_unlisted_files_404(client):
    assert client.get("/projects/20261016-153012-ab12").status_code == 404
    assert client.get("/projects/not-an-id/page").status_code == 404
    pid = create(client)
    assert client.get(f"/projects/{pid}/photos/p9.jpg").status_code == 404
    assert client.get(f"/projects/{pid}/export/page.png").status_code == 404


def test_export_returns_project_urls(client, monkeypatch):
    pid = create(client)

    def fake_export(page_url, out_dir):
        assert page_url.endswith(f"/projects/{pid}/page")
        (out_dir / "sections").mkdir(parents=True)
        files = {"png": out_dir / "page.png", "jpg": out_dir / "page.jpg", "sections": [out_dir / "sections/01-hero.png"]}
        for f in [files["png"], files["jpg"], *files["sections"]]:
            f.write_bytes(b"x")
        return files

    monkeypatch.setattr(server.render, "export", fake_export)
    data = client.post(f"/projects/{pid}/export").json()
    assert data == {"png": f"/projects/{pid}/export/page.png", "jpg": f"/projects/{pid}/export/page.jpg",
                    "sections": [f"/projects/{pid}/export/sections/01-hero.png"]}
    assert client.get(data["sections"][0]).status_code == 200
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_server.py -q`
Expected: FAIL — `ImportError: cannot import name 'server'`

- [ ] **Step 3: `pagegen/server.py` 구현**

```python
"""FastAPI app (spec §7): upload → draft, chat, undo, preview HTML, files, export."""
import copy
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import agent, render, store
from .llm import LLMError

app = FastAPI(title="pagegen")
STATIC = Path(__file__).resolve().parent.parent / "static"
# ponytail: one model job at a time for the single local user; per-project locks if this ever serves more people
_model_lock = threading.Lock()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.exception_handler(store.NotFound)
def _not_found(request, exc):
    return _error(404, "없는 프로젝트나 파일입니다.")


@app.exception_handler(store.BadInput)
def _bad_input(request, exc):
    return _error(400, str(exc))


@app.exception_handler(RequestValidationError)
def _invalid_request(request, exc):
    return _error(400, "입력이 올바르지 않습니다. 사진·상품명을 확인해 주세요.")


@app.exception_handler(store.NothingToUndo)
def _nothing_to_undo(request, exc):
    return _error(409, "되돌릴 변경이 없습니다.")


@app.exception_handler(LLMError)
def _model_error(request, exc):
    return _error(502, str(exc))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/projects")
def create_project(photos: list[UploadFile] = File(...), name: str = Form(...), notes: str = Form("")):
    if not name.strip():
        raise store.BadInput("상품명을 입력해 주세요.")
    pid, records = store.create([f.file.read(store.MAX_BYTES + 1) for f in photos])
    product = {"name": name.strip(), "notes": notes.strip()}
    with _model_lock:
        page, message = agent.make_draft(store.path(pid), pid, product, records)
    store.save(pid, page, [message])
    return {"id": pid, "version": page["version"], "messages": [message]}


@app.get("/projects/{pid}")
def get_project(pid: str):
    page, chat = store.load(pid)
    return {"page": page, "chat": chat}


class ChatIn(BaseModel):
    text: str


@app.post("/projects/{pid}/chat")
def post_chat(pid: str, body: ChatIn):
    text = body.text.strip()
    if not text:
        raise store.BadInput("메시지를 입력해 주세요.")
    with _model_lock:
        page, chat = store.load(pid)
        before = copy.deepcopy(page)
        try:
            reply, changed = agent.chat_turn(store.path(pid), page, chat, text)
        except LLMError:
            store.save_chat(pid, chat)  # keep the user's message; page.json stays as it was (spec §7)
            raise
        if changed:
            page["version"] += 1
        store.save(pid, page, chat, snapshot=before if changed else None)
    return {"reply": reply, "version": page["version"]}


@app.post("/projects/{pid}/undo")
def post_undo(pid: str):
    with _model_lock:
        return {"version": store.undo(pid)}


@app.get("/projects/{pid}/page", response_class=HTMLResponse)
def get_page(pid: str):
    page, _ = store.load(pid)
    html = render.render_html(page, lambda file: f"/projects/{pid}/{file}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/projects/{pid}/photos/{name}")
def get_photo(pid: str, name: str):
    return FileResponse(store.served_file(pid, f"photos/{name}"))


@app.get("/projects/{pid}/export/{rest:path}")
def get_export_file(pid: str, rest: str):
    return FileResponse(store.served_file(pid, f"export/{rest}"), headers={"Cache-Control": "no-store"})


@app.post("/projects/{pid}/export")
def post_export(pid: str, request: Request):
    store.load(pid)
    folder = store.path(pid)
    files = render.export(f"{request.base_url}projects/{pid}/page", folder / "export")

    def url(file: Path) -> str:
        return f"/projects/{pid}/{file.relative_to(folder).as_posix()}"

    return {"png": url(files["png"]), "jpg": url(files["jpg"]), "sections": [url(f) for f in files["sections"]]}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q`
Expected: 전체 통과

- [ ] **Step 5: 실서버 기동 확인**

Run: `uv run python -m pagegen.server` (별도 터미널) 후 `curl -s localhost:8000/ | head -3`
Expected: `<!doctype html>` 로 시작하는 index.html

- [ ] **Step 6: 커밋**

```bash
git add pagegen/server.py tests/test_server.py
git commit -m "feat: FastAPI routes for draft, chat, undo, preview and export"
```

- [ ] **Step 7: Codex에게 Task 9 브리프 발행** (`.orchestration/tasks/2026-10-16-eval.md`). Task 10은 9가 끝난 뒤 보낸다.

---

### Task 9: 실모델 회귀 측정과 프롬프트 조정 (Codex)

**Files:**
- Create: `scripts/eval_tools.py`
- Modify: `pagegen/prompts.py` (문구만: `CHAT_RULES`, 도구 `description`, `JUDGE_SYSTEM`. 스키마·도구 이름·인자·함수 시그니처는 바꾸지 않는다)

**Interfaces:**
- Consumes: `pagegen.prompts.TOOLS`, `pagegen.prompts.chat_system`, `pagegen.agent.judge`, `pagegen.llm`(실제 MLX Serve)
- Produces: `uv run python scripts/eval_tools.py` — 도구 11건·주장 검사 8건 결과표, 목표 미달이면 종료 코드 1

- [ ] **Step 1: `scripts/eval_tools.py` 작성**

```python
"""Real-model regression for the chat tools and the claim judge (spec §11). Needs MLX Serve at MLX_URL.

    uv run python scripts/eval_tools.py            # thinking on (the shipped setting)
    uv run python scripts/eval_tools.py --thinking off
"""
import argparse
import json
import sys
import time

from pagegen import agent, llm, prompts

PAGE = {
    "product": {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종"},
    "analysis": {"visible_features": ["무광 스테인리스 표면", "원터치 뚜껑"], "colors": ["실버"]},
    "photos": [{"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
               {"id": "p1-e1", "file": "photos/p1-e1.png", "origin": "edit", "source": "p1"}],
    "sections": [
        {"id": "s1", "kind": "hero", "headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러", "photo": "p1-e1"},
        {"id": "s2", "kind": "points", "headline": "이래서 좋아요", "body": "넉넉한 용량: 500ml로 하루 커피 두 잔"},
        {"id": "s3", "kind": "notice", "headline": "관리 방법", "body": "중성세제로 세척해 주세요."},
    ],
}


def calls_of(message):
    out = []
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = {"_raw": call["function"].get("arguments")}
        out.append((call["function"]["name"], args))
    return out


def has(calls, name, **expected):
    return any(n == name and all(a.get(k) == v for k, v in expected.items()) for n, a in calls)


TOOL_CASES = [
    ("헤드라인을 더 짧고 강하게 바꿔줘",
     lambda c: any(n == "update_text" and a.get("section_id") == "s1" and a.get("field") == "headline"
                   and 0 < len(a.get("text", "")) < len("하루 종일 따뜻하게") for n, a in c)),
    ("대표 사진 배경을 밝은 주방으로 바꿔줘", lambda c: has(c, "edit_photo", section_id="s1")),
    ("스펙 표 하나 추가해줘", lambda c: has(c, "add_section", kind="spec_table")),
    ("관리 방법 섹션 지워", lambda c: has(c, "remove_section", section_id="s3")),
    ("관리방법을 히어로 바로 다음으로 옮겨줘", lambda c: has(c, "move_section", section_id="s3", after_section_id="s1")),
    ("무게도 적어줘", lambda c: has(c, "ask_user")),
    ("식기세척기 사용 가능하다고 써줘",
     lambda c: any(n in ("update_text", "add_section") and "식기세척기" in json.dumps(a, ensure_ascii=False) for n, a in c)),
    ("헤드라인 짧게 하고 사진은 대리석 테이블 위로",
     lambda c: has(c, "update_text", section_id="s1", field="headline") and has(c, "edit_photo", section_id="s1")),
    ("고마워", lambda c: c == []),
    ("본문을 좀 더 감성적으로", lambda c: any(n == "update_text" and a.get("field") == "body" for n, a in c)),
    ("대표 사진 원래 사진으로 되돌려", lambda c: has(c, "use_photo", section_id="s1", photo_id="p1")),
]

BASIS = "근거 텍스트: 스테인리스 텀블러\n이중 진공 단열, 500ml, 색상 3종\n사진에서 보이는 것: 무광 스테인리스 표면, 원터치 뚜껑, 실버"
JUDGE_CASES = [  # (text, extra user message added to the basis, should be flagged)
    ("하루 종일 따뜻하고 차갑게", "", True),
    ("식기세척기 사용 가능", "", True),
    ("12시간 보냉 유지", "", True),
    ("식기세척기 사용 가능", "식기세척기 써도 돼요", False),
    ("이중 진공 단열로 따뜻함이 오래가요", "", False),
    ("하루를 산뜻하게", "", False),
    ("당신의 책상 위 작은 여유", "", False),
    ("손에 착 감기는 그립감으로 어디서나 함께해요", "", False),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thinking", choices=["on", "off"], default="on")
    thinking = parser.parse_args().thinking == "on"
    system = prompts.chat_system(PAGE)

    tool_ok = 0
    for i, (text, check) in enumerate(TOOL_CASES, 1):
        started = time.perf_counter()
        message = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": text}],
                           tools=prompts.TOOLS, thinking=thinking, max_tokens=1024)
        calls = calls_of(message)
        ok = check(calls)
        tool_ok += ok
        print(f"tool #{i:02d} {'OK ' if ok else 'BAD'} {time.perf_counter() - started:5.2f}s {text} -> "
              f"{json.dumps(calls, ensure_ascii=False)[:160]} {('| ' + message.get('content', '')[:60]) if not calls else ''}")

    judge_ok = 0
    for text, said, should_flag in JUDGE_CASES:
        started = time.perf_counter()
        try:
            flagged = agent.judge(llm, text, BASIS + (f"\n{said}" if said else ""))
        except llm.LLMError as e:
            flagged = [f"ERROR {e}"]
        ok = bool(flagged) == should_flag
        judge_ok += ok
        print(f"judge {'OK ' if ok else 'BAD'} {time.perf_counter() - started:5.2f}s {text!r} -> {flagged}")

    print(f"\ntools {tool_ok}/{len(TOOL_CASES)} (target ≥ 9)  judge {judge_ok}/{len(JUDGE_CASES)} (target 8)")
    return 0 if tool_ok >= 9 and judge_ok == len(JUDGE_CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 기준선 측정** — 프롬프트를 고치기 전에 한 번 돌려 결과를 그대로 보관한다.

Run: `uv run python scripts/eval_tools.py | tee .orchestration/reports/2026-10-16-eval/baseline-thinking-on.txt`
Expected: 결과표와 마지막 줄 `tools N/11 … judge M/8`. 목표 미달이면 종료 코드 1.

- [ ] **Step 3: 프롬프트 조정 반복** — 실패한 사례만 보고 `pagegen/prompts.py`의 `CHAT_RULES`, 도구 `description`, `JUDGE_SYSTEM` 문구를 고친다. 한 번 고칠 때마다:
  1. `uv run pytest -q` (단위 테스트는 프롬프트 문구에 의존하지 않으므로 그대로 통과해야 한다)
  2. `uv run python scripts/eval_tools.py | tee .orchestration/reports/2026-10-16-eval/iter-<n>.txt`
  
  목표(도구 ≥ 9/11, 판정 8/8)에 닿거나 5회를 채우면 멈춘다. 특정 평가 문장을 프롬프트에 그대로 넣어 맞히지 않는다(과적합 금지). 예시는 다른 상품·다른 표현으로 든다.

- [ ] **Step 4: thinking 끔 비교 1회**

Run: `uv run python scripts/eval_tools.py --thinking off | tee .orchestration/reports/2026-10-16-eval/final-thinking-off.txt`

- [ ] **Step 5: 보고** — 브리프 하단 `## 결과`: 기준선 → 최종 점수표(thinking on/off), 바꾼 프롬프트 diff 요약과 이유, 남은 실패 사례 원문, 확인한 것과 추정 구분. Claude가 검수 후 커밋한다:

```bash
git add scripts/eval_tools.py pagegen/prompts.py
git commit -m "feat: real-model eval for chat tools and claim judge; tune prompts"
```

---

### Task 10: 처음부터 끝까지 실서버 확인과 README (Codex)

**Files:**
- Create: `scripts/smoke.py`, `README.md`

**Interfaces:**
- Consumes: 실행 중인 `python -m pagegen.server`(:8000)와 MLX Serve(:11234), 전체 API(스펙 §7)
- Produces: `uv run python scripts/smoke.py --photo <사진>` — 결과 JSON과 사진을 `--out` 폴더에 남긴다

- [ ] **Step 1: `scripts/smoke.py` 작성**

```python
"""End-to-end check against a running server (spec §11).

    uv run python -m pagegen.server 2> server.log &
    uv run python scripts/smoke.py --photo product.jpg --out .orchestration/reports/2026-10-16-smoke

Makes three projects from one photo cropped square, landscape (4:3) and portrait (3:4): each runs the first
draft (with its staged scene edit). The square project then gets three chat edits and an export.
"""
import argparse
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

CHATS = ["헤드라인을 더 짧고 강하게 바꿔줘", "무게는 350g이에요. 스펙 표에 넣어줘", "대표 사진 배경을 밝은 대리석 테이블로 바꿔줘"]


def crops(path: Path) -> dict[str, bytes]:
    im = Image.open(path).convert("RGB")
    w, h = im.size

    def center(rw: int, rh: int) -> bytes:
        scale = min(w / rw, h / rh)
        cw, ch = int(rw * scale), int(rh * scale)
        box = ((w - cw) // 2, (h - ch) // 2, (w + cw) // 2, (h + ch) // 2)
        buffer = io.BytesIO()
        im.crop(box).save(buffer, "JPEG", quality=92)
        return buffer.getvalue()

    return {"square": center(1, 1), "landscape": center(4, 3), "portrait": center(3, 4)}


def timed(fn, *args, **kwargs):
    started = time.perf_counter()
    response = fn(*args, **kwargs)
    return response, round(time.perf_counter() - started, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--name", default="스테인리스 텀블러")
    parser.add_argument("--notes", default="이중 진공 단열, 500ml, 색상 3종. 중성세제로 손세척 권장")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, default=Path(".orchestration/reports/smoke"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=args.server, timeout=600)
    report, failed = {}, False

    for label, data in crops(args.photo).items():
        r, seconds = timed(client.post, "/projects", data={"name": args.name, "notes": args.notes},
                           files=[("photos", (f"{label}.jpg", data, "image/jpeg"))])
        entry = {"status": r.status_code, "draft_seconds": seconds, "body": r.json()}
        report[label] = entry
        print(f"{label}: draft {r.status_code} in {seconds}s")
        if r.status_code != 200:
            failed = True
            continue
        pid = entry["body"]["id"]
        page = client.get(f"/projects/{pid}").json()["page"]
        entry["sections"] = [(s["kind"], s["headline"], s["body"]) for s in page["sections"]]
        scene = next((s for s in page["sections"] if s["kind"] == "scene"), None)
        if scene:
            photo = next(p for p in page["photos"] if p["id"] == scene["photo"])
            (args.out / f"{label}-scene-{photo['id']}.png").write_bytes(client.get(f"/projects/{pid}/{photo['file']}").content)
        if label != "square":
            continue
        entry["chats"] = []
        for text in CHATS:
            r, seconds = timed(client.post, f"/projects/{pid}/chat", json={"text": text})
            entry["chats"].append({"text": text, "status": r.status_code, "seconds": seconds, "body": r.json()})
            print(f"  chat {r.status_code} in {seconds}s: {text} -> {r.json().get('reply', r.json()).get('text', r.json())}")
            failed |= r.status_code != 200
        r, seconds = timed(client.post, f"/projects/{pid}/export")
        entry["export"] = {"status": r.status_code, "seconds": seconds, "body": r.json()}
        print(f"  export {r.status_code} in {seconds}s")
        if r.status_code == 200:
            png = client.get(r.json()["png"]).content
            (args.out / "page.png").write_bytes(png)
            entry["export"]["png_size"] = Image.open(io.BytesIO(png)).size
        else:
            failed = True

    (args.out / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(f"report: {args.out / 'smoke.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 실행**

```bash
uv run python -m pagegen.server 2> .orchestration/reports/2026-10-16-smoke/server.log &
uv run python scripts/smoke.py --photo <일반 상품 사진 경로> --out .orchestration/reports/2026-10-16-smoke
grep -c "JSON retry" .orchestration/reports/2026-10-16-smoke/server.log
```

사진은 일반 상품이 찍힌 것을 쓴다(없으면 `../page-generation-improve/data/evaluation/cma_real_v1/images/cma-101636.jpg`). 끝나면 띄운 pagegen 서버만 종료한다(MLX Serve는 그대로).

Expected: 종료 코드 0, `page.png` 폭 860.

- [ ] **Step 3: 육안 확인과 성공 기준 대조** — 보고서에 아래를 적는다.
  - 초안 시간(3건) ≤ 120초인지, 문구 수정 ≤ 30초·사진 편집 포함 ≤ 90초인지 (스펙 §1)
  - `*-scene-*.png` 3장: 제품이 늘어나거나 잘리지 않았는지(정사각·가로·세로 각각)
  - 초안 문구에 설명에 없는 숫자가 있는지(`smoke.json`의 `sections`)
  - 두 번째 채팅 뒤 스펙 표에 `무게: 350g`이 들어갔는지(사용자가 말한 숫자는 허용)
  - `JSON retry` 횟수
  - 브라우저로 `http://127.0.0.1:8000`을 열어 업로드 → 채팅 → 되돌리기 → 내보내기를 한 번 해 보고, 한글 입력 중 Enter가 글자를 자르지 않는지 확인

- [ ] **Step 4: `README.md` 작성** — 아래 내용을 한국어로, 실제 측정값을 넣어 쓴다.

```markdown
# pagegen — 로컬 대화형 상세페이지 에이전트

상품 사진과 설명을 올리면 상세페이지 초안을 만들고, 채팅으로 고쳐 폭 860px 이미지로 내보냅니다.
모든 모델은 이 Mac의 MLX Serve에서 돕니다(Qwen3.8-9B 4bit, Qwen-Image 2.1 4bit).

## 준비
1. MLX Serve 26.10.1 이상이 `http://127.0.0.1:11234`에서 두 모델을 서빙해야 합니다.
2. `uv sync && uv run playwright install chromium`

## 실행
uv run python -m pagegen.server   # http://127.0.0.1:8000

## 설정 (환경 변수)
MLX_URL, TEXT_MODEL, IMAGE_MODEL, IMAGE_STEPS, PROJECTS_DIR — 기본값은 스펙 §2

## 지키는 규칙
- 업로드 원본은 바꾸지 않습니다. 사진 편집은 새 파일입니다.
- 입력·대화에 없는 숫자와 제품 사실은 페이지에 들어가지 않습니다.

## 측정 (YYYY-MM-DD, 이 Mac)
초안 N초, 문구 수정 N초, 사진 편집 N초 — scripts/smoke.py 결과

## 테스트
uv run pytest
uv run python scripts/eval_tools.py   # 실모델 회귀
```

- [ ] **Step 5: 보고** — 브리프 하단 `## 결과`: 단계별 시간표, 성공 기준 대조, 사진 비율 육안 판정(확인/추정 구분), 실패·이상 원문, 산출물 경로. Claude가 검수 후 커밋한다:

```bash
git add scripts/smoke.py README.md
git commit -m "feat: end-to-end smoke script and README with measured timings"
```

---

## 완료 조건

- `uv run pytest -q` 전체 통과
- `scripts/eval_tools.py`: 도구 ≥ 9/11, 주장 검사 8/8 (미달이면 남은 실패와 함께 사용자에게 보고하고 결정을 받는다)
- `scripts/smoke.py`: 종료 코드 0, 스펙 §1 성공 기준 대조표가 보고됨
- 스펙 §9의 "아직 재지 않았다" 항목(7개 도구 계약, `json_schema` 준수, 패딩 효과)이 측정값으로 갱신됨
