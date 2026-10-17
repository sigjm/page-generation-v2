# 주장 판정 파인튜닝 구현 계획 (1단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원본 Qwen3.8-9B를 주장 판정 한 가지로 LoRA 학습해, 평가셋에서 근거 없는 주장 F1을 10%p 이상 올리고 잘못 거르기·판정 실패를 줄인 판정 전용 모델을 만든다.

**Architecture:** Codex가 가상 상품과 정답을 만들고(`codex exec --output-schema`), 원본 9B가 실제 문구를 쓴다. 정답을 기계 검사로 거른 뒤 런타임과 똑같은 프롬프트로 mlx-vlm SFT 행을 만든다. 학습한 LoRA를 4bit 모델에 병합해 `JUDGE_MODEL`로 판정 호출에만 쓴다.

**Tech Stack:** 앱은 기존과 같음. 학습 전용: `.local/venvs/mlx-vlm`(Python 3.13, mlx-vlm 0.7.8), Codex CLI(`codex exec`).

**Spec:** `docs/superpowers/specs/2026-10-16-judge-finetune-design.md`

## Global Constraints

- 데이터·어댑터·로그는 `.local/finetune/judge/`(git 제외). 사용자 데이터 `projects/`는 쓰지 않는다.
- 학습 의존성은 `pyproject.toml`에 넣지 않는다.
- 판정 user 메시지는 런타임과 같은 함수(`agent.judge_messages`)로만 만든다.
- 정답 형식은 `prompts.JUDGE_SCHEMA` 그대로, assistant 내용은 공백 없는 JSON(`separators=(",", ":")`, `ensure_ascii=False`).
- 원본 모델: `~/.mlx-serve/models/empero-ai/Qwen3.8-9B-Distill-MLX-4bit`. 병합 모델: `~/.mlx-serve/models/local/Qwen3.8-9B-pagegen-judge-r1`.
- MLX Serve 재시작은 사용자에게 묻고 한다. Codex는 git 상태를 바꾸지 않는다.

## Review Focus

1. **정답의 `phrase`가 문장에 없는 경우(교사가 말을 바꿔 씀)** → 버려야 한다. (Task 3: `test_validate_label_rejects_phrase_not_in_text`)
2. **끼워 넣은 문장인데 정답이 그 구절을 근거 있음으로 매긴 경우** → 버려야 한다. (Task 3: `test_validate_label_requires_injected_phrase_flagged`)
3. **같은 상품의 문장이 평가셋과 학습셋에 함께 들어가는 경우** → 상품 단위로만 나뉘어야 한다. (Task 3: `test_split_is_by_product`)
4. **판정 호출이 실패(LLMError)한 문장** → 평가에서 "걸렀음"으로 세고(런타임은 fail-closed), 실패 수도 따로 보고한다. (Task 5: `test_metrics_count_failures_as_flagged`)
5. **`JUDGE_MODEL`을 설정하지 않은 경우** → 지금과 똑같이 `TEXT_MODEL`로 판정한다. (Task 1: `test_judge_model_defaults_to_text_model`)

## 순서와 분담

```text
Claude: 1 ─→ 3(코드) ─────────────→ 4(평가셋 검토) → 5 → 6(학습) → 7(적용·판정)
Codex:   └→ 2(상품) ─→ [Claude: 문장 생성 3a] ─→ 2b(끼워 넣기·정답)
```

---

### Task 1: `JUDGE_MODEL`과 `judge_messages` (Claude)

**Files:** Modify `pagegen/llm.py`, `pagegen/agent.py`, `tests/test_llm.py`, `tests/test_agent.py`, `.gitignore`

**Interfaces — Produces:** `llm.JUDGE_MODEL`(env, 기본 `TEXT_MODEL`), `llm.chat(..., model=None)`, `llm.chat_json(..., model=None)`, `agent.judge_messages(text, basis) -> list[dict]`. `agent.judge`는 `model=llm.JUDGE_MODEL`로 부른다.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_llm.py` 끝:

```python
def test_judge_model_defaults_to_text_model():
    assert llm.JUDGE_MODEL == llm.TEXT_MODEL  # no env set in tests


def test_chat_uses_the_given_model(monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "_post", lambda route, body, timeout=300: sent.update(body) or {"choices": [{"message": {"content": "{}"}}]})
    llm.chat_json([], {}, model="local/judge")
    assert sent["model"] == "local/judge"
    llm.chat([])
    assert sent["model"] == llm.TEXT_MODEL
```

`tests/test_agent.py`의 `FakeLLM`에 클래스 속성 `JUDGE_MODEL = "judge-model"`을 넣고, `chat_json`이 받은 `kw`를 `self.json_kw`에 쌓게 한다(`self.json_kw = []`를 `__init__`에, `self.json_kw.append(kw)`를 `chat_json`에). 그리고 끝에:

```python
def test_judge_calls_use_the_judge_model(page, folder):
    fake = FakeLLM(chats=[{"tool_calls": [call("update_text", section_id="s1", field="headline", text="온종일 따뜻")]},
                          {"content": "줄였어요."}], jsons=[OK])
    agent.chat_turn(folder, page, [], "헤드라인 짧게", fake)
    assert fake.json_kw[0]["model"] == "judge-model"


def test_judge_messages_match_the_runtime_prompt():
    from pagegen import prompts
    assert agent.judge_messages("문장", "근거") == [{"role": "system", "content": prompts.JUDGE_SYSTEM},
                                                  {"role": "user", "content": "근거\n새 문구: 문장"}]
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_llm.py tests/test_agent.py -q` → 4 failed
- [ ] **Step 3: 구현**

`pagegen/llm.py`: `IMAGE_STEPS` 아래에 `JUDGE_MODEL = os.environ.get("JUDGE_MODEL", TEXT_MODEL)`. `chat` 시그니처에 `model: str | None = None`을 더하고 본문 `"model": model or TEXT_MODEL`. `chat_json(messages, schema, *, max_tokens=1500, model=None)`이 `chat(..., model=model)`로 넘긴다.

`pagegen/agent.py`:

```python
def judge_messages(text: str, basis: str) -> list[dict]:
    """The exact judge prompt; the fine-tuning data (scripts/finetune) is built with this same function."""
    return [{"role": "system", "content": prompts.JUDGE_SYSTEM}, {"role": "user", "content": f"{basis}\n새 문구: {text}"}]


def judge(llm, text: str, basis: str) -> list[str]:
    """Phrases of `text` asserting product facts missing from `basis` (spec §5). Raises LLMError: fail-closed."""
    if not text.strip():
        return []
    data = llm.chat_json(judge_messages(text, basis), prompts.JUDGE_SCHEMA, max_tokens=800, model=llm.JUDGE_MODEL)
    return [str(p.get("phrase") or text) for p in data.get("phrases", [])
            if p.get("type") != "mood" and p.get("supported") is not True]
```

`.gitignore`에 `.local/` 한 줄.

- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 통과
- [ ] **Step 5: 커밋** — `feat: JUDGE_MODEL for judge calls; judge_messages shared with fine-tuning`
- [ ] **Step 6: Codex에게 Task 2 브리프 발행**

---

### Task 2: Codex 교사 — 가상 상품, 끼워 넣은 문장, 정답 (Codex)

**Files:** Create `scripts/finetune/codex_teacher.py`. 산출물 `.local/finetune/judge/{products,injected,labels}.jsonl`.

**Interfaces — Consumes:** `pagegen.agent.basis_of`, `pagegen.prompts.JUDGE_SYSTEM`·`JUDGE_SCHEMA`, Task 3a의 `.local/finetune/judge/sentences.jsonl`. **Produces:** 아래 세 파일(형식 고정).

`codex_teacher.py`는 하위 명령 3개를 가진다. 모두 `codex exec -s read-only --skip-git-repo-check --output-schema <스키마 파일> -o <답 파일> "<지시>"`를 `subprocess`로 부르고, 호출 결과를 `.local/finetune/judge/cache/<명령>/<키>.json`에 저장해 다시 실행하면 이어서 한다(`--workers N` 병렬, 기본 4). 실패한 호출은 2번까지 다시 시도하고, 그래도 실패하면 건너뛰고 개수를 보고한다.

**`products`** — 분류 5개 × 6회 × 10개 = 300개.

- 지시(분류·이미 쓴 이름을 채워서):

```text
너는 한국 온라인 쇼핑몰의 가상 상품 데이터를 만든다. 분류 "{category}"의 서로 다른 가상 상품 10개를 만든다.
- name: 실제로 팔릴 법한 상품명. 실존 브랜드·상표·인물 이름은 쓰지 않는다. 이미 쓴 이름과 겹치지 않는다: {used_names}
- notes: 판매자가 쓴 설명 2~5문장. 상품에 따라 숫자(용량·크기·무게·개수), 소재·원재료, 사용법, 관리·보관법을 섞는다. 10개 중 2개는 1문장짜리 빈약한 설명으로 쓴다.
- visible_features: 상품 사진에서 눈으로 보일 특징 2~5개(모양·부품·무늬·질감). 보이지 않는 성능·소재는 쓰지 않는다.
- colors: 사진에서 보일 색 1~3개.
```

- 스키마: `{"products": [{"name", "notes", "visible_features": [str], "colors": [str]}]}`(모든 필드 필수, 배열 길이 10).
- 출력 한 줄: `{"id": "<분류영문>-<NNN>", "category", "name", "notes", "visible_features", "colors"}`. 분류 영문: 의류·잡화 `fashion`, 식품 `food`, 화장품 `beauty`, 가전·전자 `electronics`, 생활·주방 `living`.
- 검사: id·name 중복 없음, 각 분류 60개, notes 1~5문장.

**`inject`** — `sentences.jsonl`(Task 3a)에서 상품마다 9B 문장 최대 4개를 골라 근거 없는 제품 사실 하나를 끼운 변형을 만든다.

```text
아래 근거와 문장들이 있다. 문장마다, 근거에 없는 제품 사실 하나(기능, 성능, 소재, 인증, 사용·관리 방법, 효능 중 하나)를 자연스럽게 끼워 넣은 새 문장을 만든다. 끼워 넣은 부분은 injected_phrase에 원문 그대로 적는다. 분위기·감정 표현은 사실이 아니므로 끼워 넣지 않는다. 숫자는 쓰지 않는다(숫자는 다른 검사가 막는다).
근거:
{basis}
문장:
{numbered_sentences}
```

- 스키마: `{"items": [{"n": int, "text": str, "injected_phrase": str}]}`.
- 출력 한 줄: `{"id": "<원문id>-inj", "product_id", "text", "source": "injected", "injected_phrase"}`. `injected_phrase`가 `text`에 공백 무시하고 없으면 버린다.
- 양: 최종 데이터에서 근거 없는 문장이 35~50%가 되도록, 정답 1차 결과(아래 `label`)를 본 뒤 상품당 개수(2~4)를 정한다.

**`label`** — `sentences.jsonl` + `injected.jsonl`의 모든 문장. 상품 하나의 문장을 20개씩 묶어 부른다.

```text
{JUDGE_SYSTEM}

정답 매기기 규칙(교사용):
- 문장마다 짧은 구절로 나누고 모든 구절을 빠짐없이 분류한다. phrase는 문장에 있는 글자 그대로 옮긴다(바꿔 쓰지 않는다).
- 분위기·감정·장면·추천·주관적 꾸밈말은 mood(supported=true).
- 제품의 기능·성능·소재·크기·인증·호환·사용 가능 여부·사용/관리 지시·제조 방식·품질·내구성을 단정하면 fact. 근거 텍스트나 사진에서 보이는 것에 같은 뜻이 있으면 supported=true, 없거나 추측이 필요하면 false. 애매하면 false.
- 근거 텍스트에 있는 판매자 주장은 근거로 본다.

{basis}
문장:
{numbered_sentences}
```

- 스키마: `{"items": [{"n": int, "phrases": [{"phrase": str, "type": "fact"|"mood", "supported": bool}]}]}`.
- 출력 한 줄: `{"id": "<문장id>", "phrases": [...], "teacher": "codex"}`.

- [ ] **Step 1:** `codex_teacher.py`를 쓰고 `products` 하위 명령을 실행한다. 분류별 개수·중복을 확인해 보고한다.
- [ ] **Step 2 (Task 3a 뒤):** `label`을 `sentences.jsonl`에 먼저 돌려 근거 없는 비율을 잰다 → `inject` 개수를 정해 실행 → `injected.jsonl`에 `label`을 돌린다.
- [ ] **Step 3: 보고** — 파일별 개수, 버린 개수와 이유, 근거 없는 문장 비율, 호출 수·실패 수·걸린 시간. Claude가 검수 후 스크립트를 커밋한다(데이터는 git 제외).

---

### Task 3: 9B 문장 생성과 SFT 데이터 만들기 (Claude)

**Files:** Create `scripts/finetune/judge_data.py`, `tests/test_finetune.py`

**Interfaces — Consumes:** `products.jsonl`(Task 2), `labels.jsonl`·`injected.jsonl`(Task 2b), `agent.judge_messages`, `agent.basis_of`, `agent._sentences`, `prompts.COPY_*`. **Produces:** `sentences.jsonl`(3a), `sft/{train,validation}.jsonl`, `eval_candidates.jsonl`(3b).

- [ ] **Step 1: 실패하는 테스트** (`tests/test_finetune.py`)

```python
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
```


- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_finetune.py -q` → ImportError
- [ ] **Step 3: `scripts/finetune/judge_data.py` 구현**

```python
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
from pagegen import agent, llm, prompts  # noqa: E402
from pagegen.page import is_sourced  # noqa: E402

ROOT = Path(".local/finetune/judge")
HELD_CATEGORY_COUNT = 6  # products per category kept out of training for the eval set


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
    random.Random(0).shuffle(rows["train"])
    write("sft/train.jsonl", rows["train"])
    write("sft/validation.jsonl", rows["valid"])
    held_flagged = [r for r in rows["held"] if r["flagged"]]
    held_clean = [r for r in rows["held"] if not r["flagged"]]
    rng = random.Random(0)
    write("eval_candidates.jsonl", rng.sample(held_flagged, min(75, len(held_flagged))) + rng.sample(held_clean, min(75, len(held_clean))))
    train_flagged = sum('"supported":false' in r["messages"][-1]["content"] for r in rows["train"])
    print(json.dumps({"train": len(rows["train"]), "validation": len(rows["valid"]), "held": len(rows["held"]),
                      "dropped": dropped, "train_flagged_ratio": round(train_flagged / max(1, len(rows["train"])), 3)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sentences", "build"])
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    return cmd_sentences(args.workers) if args.command == "sentences" else cmd_build()


if __name__ == "__main__":
    sys.exit(main())
```

참고: `flagged`의 판정식(`type != "mood"` and `supported is not True`)은 `agent.judge`가 근거 없는 구절을 고르는 식과 같다. 학습 SFT 행에서 `train_flagged_ratio`를 셀 때는 근사(`"supported":false` 포함 여부)로 충분하다.

- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 통과
- [ ] **Step 5: 커밋** — `feat: claim-judge fine-tuning data builder`
- [ ] **Step 6 (3a): 문장 생성 실행** — Task 2 Step 1(상품) 뒤: `uv run python scripts/finetune/judge_data.py sentences --workers 2`. 상품 300 × 2호출. 끝나면 Codex에게 Task 2 Step 2를 보낸다.
- [ ] **Step 7 (3b): 빌드** — Task 2 Step 2 뒤: `uv run python scripts/finetune/judge_data.py build`. 근거 없는 비율(`train_flagged_ratio`)이 0.35~0.50 밖이면 Codex에게 `inject` 양 조정을 요청하고 다시 빌드한다.

---

### Task 4: 평가셋 검토 (Claude)

**Files:** `.local/finetune/judge/eval_candidates.jsonl` → `.local/finetune/judge/eval.jsonl`(git 제외), 검토 기록 `docs/finetune/2026-10-16-judge-eval-review.md`

- [ ] **Step 1:** 150문장을 근거와 함께 하나씩 읽고, 정답(`phrases`)을 스펙 §3.3 규칙으로 확인한다. 틀린 정답은 고치고 `"reviewed": "fixed"`, 맞으면 `"reviewed": "ok"`를 붙여 `eval.jsonl`로 쓴다(`flagged`도 다시 계산).
- [ ] **Step 2:** 검토 기록 문서에 고친 개수와 대표 사례(문장·Codex 정답·고친 정답·이유)를 적는다. Codex 정답 오류율이 10%를 넘으면 멈추고 사용자에게 보고한다(스펙 §8).
- [ ] **Step 3: 커밋** — 검토 기록 문서만(`docs: judge eval set review`).

---

### Task 5: 판정 평가 스크립트와 원본 기준선 (Claude)

**Files:** Create `scripts/finetune/eval_judge.py`; Modify `tests/test_finetune.py`

**Interfaces — Consumes:** `eval.jsonl`, `agent.judge`, `llm.JUDGE_MODEL`. **Produces:** `metrics(results) -> dict`, CLI 출력과 `--out` JSON.

- [ ] **Step 1: 실패하는 테스트** (`tests/test_finetune.py` 끝)

```python
from scripts.finetune import eval_judge as ej


def test_metrics_count_failures_as_flagged():
    results = [{"gold": True, "pred": True, "failed": False}, {"gold": True, "pred": False, "failed": False},
               {"gold": False, "pred": False, "failed": False}, {"gold": False, "pred": True, "failed": True}]
    m = ej.metrics(results)
    assert (m["precision"], m["recall"]) == (0.5, 0.5)
    assert m["f1"] == 0.5 and m["false_flag_rate"] == 0.5 and m["failures"] == 1
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_finetune.py -q` → ImportError
- [ ] **Step 3: 구현**

```python
"""Score the claim judge on the reviewed eval set (spec 2026-10-16-judge-finetune §6).

    uv run python scripts/finetune/eval_judge.py --model empero-ai/Qwen3.8-9B-Distill-MLX-4bit --out .local/finetune/judge/eval-base.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pagegen import agent, llm  # noqa: E402


def metrics(results: list[dict]) -> dict:
    tp = sum(r["gold"] and r["pred"] for r in results)
    fp = sum(not r["gold"] and r["pred"] for r in results)
    fn = sum(r["gold"] and not r["pred"] for r in results)
    clean = sum(not r["gold"] for r in results)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"n": len(results), "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "false_flag_rate": round(fp / clean, 3) if clean else 0.0, "failures": sum(r["failed"] for r in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--eval", type=Path, default=Path(".local/finetune/judge/eval.jsonl"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    llm.JUDGE_MODEL = args.model
    results = []
    for row in (json.loads(line) for line in args.eval.read_text("utf-8").splitlines() if line.strip()):
        started, failed = time.perf_counter(), False
        try:
            pred = bool(agent.judge(llm, row["text"], row["basis"]))
        except llm.LLMError:
            pred, failed = True, True  # runtime is fail-closed: a failed judge drops the sentence
        results.append({"id": row["id"], "text": row["text"], "gold": row["flagged"], "pred": pred, "failed": failed,
                        "seconds": round(time.perf_counter() - started, 2)})
    m = metrics(results) | {"model": args.model, "avg_seconds": round(sum(r["seconds"] for r in results) / len(results), 2)}
    print(json.dumps(m, ensure_ascii=False))
    for r in results:
        if r["gold"] != r["pred"]:
            print(f"{'MISS' if r['gold'] else 'FALSE'} {r['text']}")
    if args.out:
        args.out.write_text(json.dumps({"metrics": m, "results": results}, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인** — `uv run pytest -q`
- [ ] **Step 5: 원본 기준선** — `uv run python scripts/finetune/eval_judge.py --model empero-ai/Qwen3.8-9B-Distill-MLX-4bit --out .local/finetune/judge/eval-base.json`
- [ ] **Step 6: 커밋** — `feat: claim-judge eval script` (기준선 수치는 Task 7 보고서에)

---

### Task 6: 학습과 병합 (Claude)

**Files:** Create `scripts/finetune/fuse_lora.py`(v1 `scripts/dataset/fuse_lora.py`에서 가져옴), 로그 `.local/finetune/judge/lora/r1.log`

- [ ] **Step 1: 환경** — `uv venv -p 3.13 .local/venvs/mlx-vlm && uv pip install -p .local/venvs/mlx-vlm/bin/python mlx-vlm==0.7.8 torch torchvision`
- [ ] **Step 2: 학습(백그라운드)**

```bash
.local/venvs/mlx-vlm/bin/python -m mlx_vlm.train \
  --model ~/.mlx-serve/models/empero-ai/Qwen3.8-9B-Distill-MLX-4bit \
  --data .local/finetune/judge/sft --train-on-completions --grad-checkpoint --max-seq-length 2048 \
  --lora-rank 16 --lora-alpha 32 --learning-rate 5e-5 --epochs 1 \
  --output-path .local/finetune/judge/lora/r1 2>&1 | tee .local/finetune/judge/lora/r1.log
```

(mlx-vlm 0.7.8의 실제 인자 이름은 `--help`로 확인하고, 이름이 다르면 같은 뜻의 인자로 바꾸고 ledger에 적는다.)
- [ ] **Step 3: 메모리 확인** — 시작 후 50스텝 동안 `footprint`(또는 `vm_stat`)로 시스템 여유 메모리를 잰다. 여유가 8GB 미만이면 학습을 멈추고 사용자에게 알린다(MLX Serve를 잠시 내릴지 묻는다).
- [ ] **Step 4: 병합** — `fuse_lora.py`를 v1에서 그대로 가져와 docstring 경로만 바꾸고: `.local/venvs/mlx-vlm/bin/python scripts/finetune/fuse_lora.py --model ~/.mlx-serve/models/empero-ai/Qwen3.8-9B-Distill-MLX-4bit --adapter .local/finetune/judge/lora/r1 --out ~/.mlx-serve/models/local/Qwen3.8-9B-pagegen-judge-r1`
- [ ] **Step 5: 커밋** — `feat: fuse script for the judge LoRA`

---

### Task 7: 적용 판단 (Claude)

- [ ] **Step 1:** 사용자에게 MLX Serve 재시작을 묻는다(새 모델 폴더를 읽기 위해). 허락을 받으면 같은 앱 설정으로 다시 띄운다.
- [ ] **Step 2:** `uv run python scripts/finetune/eval_judge.py --model local/Qwen3.8-9B-pagegen-judge-r1 --out .local/finetune/judge/eval-r1.json`
- [ ] **Step 3:** `JUDGE_MODEL=local/Qwen3.8-9B-pagegen-judge-r1 uv run python scripts/eval_tools.py` (판정 8/8 확인)
- [ ] **Step 4:** 결과 보고서 `docs/finetune/2026-10-16-judge-r1-report.md`: 데이터 개수, 학습 시간·메모리, 원본 vs r1 표(F1·정밀도·재현율·잘못 거르기·실패·지연), 틀린 문장 대표 사례, 합격 여부.
- [ ] **Step 5:** 합격이면 README의 설정 표에 `JUDGE_MODEL`을 추가하고, 서버를 `JUDGE_MODEL=local/Qwen3.8-9B-pagegen-judge-r1`로 다시 띄운다. 불합격이면 README는 그대로 두고 원인과 다음 시도를 보고서에 적는다.
- [ ] **Step 6: 커밋** — 보고서와 README.

## 완료 조건

- `uv run pytest -q` 전체 통과
- 보고서에 원본 vs r1 비교와 합격 판정이 있다
- 합격이면 `JUDGE_MODEL`로 서버가 r1을 쓰고, 불합격이면 원본 그대로
