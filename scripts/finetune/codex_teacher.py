"""Codex teacher data generator for judge fine-tuning.

All generated data, schemas, and resumable Codex responses live under
``.local/finetune/judge``.  The Codex subprocess itself runs from a temporary
directory outside the repository with a read-only sandbox.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pagegen.agent import basis_of  # noqa: E402
from pagegen import prompts  # noqa: E402


DATA_DIR = ROOT / ".local/finetune/judge"
SCHEMA_DIR = DATA_DIR / "schemas"
CACHE_DIR = DATA_DIR / "cache"
PRODUCTS_PATH = DATA_DIR / "products.jsonl"
SENTENCES_PATH = DATA_DIR / "sentences.jsonl"
INJECTED_PATH = DATA_DIR / "injected.jsonl"
LABELS_PATH = DATA_DIR / "labels.jsonl"
CODEX_CWD = Path(tempfile.gettempdir()) / "page-generation-v2-codex-teacher"

CATEGORIES = [
    ("fashion", "의류·잡화"),
    ("food", "식품"),
    ("beauty", "화장품"),
    ("electronics", "가전·전자"),
    ("living", "생활·주방"),
]
PRODUCTS_PER_BATCH = 10
BATCHES_PER_CATEGORY = 6
MAX_RETRIES = 2
CODEX_TIMEOUT_SECONDS = 600


class BatchValidationError(ValueError):
    def __init__(self, message: str, discarded: int = 0):
        super().__init__(message)
        self.discarded = discarded


class RunStats:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.calls = 0
        self.failures = 0
        self.retries = 0
        self.cache_hits = 0
        self.discarded = 0
        self.discard_reasons: Counter[str] = Counter()
        self.lock = threading.Lock()

    def add(self, field: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, field, getattr(self, field) + amount)

    def discard(self, count: int, reason: str) -> None:
        if count <= 0:
            return
        with self.lock:
            self.discarded += count
            self.discard_reasons[reason] += count

    @property
    def elapsed(self) -> float:
        return round(time.perf_counter() - self.started, 1)


def _string_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def schema_documents() -> dict[str, dict[str, Any]]:
    product_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "notes", "visible_features", "colors"],
        "properties": {
            "name": _string_schema(),
            "notes": _string_schema(),
            "visible_features": {"type": "array", "items": _string_schema(), "minItems": 2, "maxItems": 5},
            "colors": {"type": "array", "items": _string_schema(), "minItems": 1, "maxItems": 3},
        },
    }
    injected_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["n", "text", "injected_phrase"],
        "properties": {"n": {"type": "integer"}, "text": _string_schema(), "injected_phrase": _string_schema()},
    }
    phrase_schema = prompts.JUDGE_SCHEMA["properties"]["phrases"]["items"]
    label_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["n", "phrases"],
        "properties": {
            "n": {"type": "integer"},
            "phrases": {"type": "array", "items": phrase_schema, "minItems": 1},
        },
    }
    return {
        "products": {
            "type": "object", "additionalProperties": False, "required": ["products"],
            "properties": {"products": {"type": "array", "items": product_item,
                                            "minItems": PRODUCTS_PER_BATCH, "maxItems": PRODUCTS_PER_BATCH}},
        },
        "inject": {
            "type": "object", "additionalProperties": False, "required": ["items"],
            "properties": {"items": {"type": "array", "items": injected_item}},
        },
        "label": {
            "type": "object", "additionalProperties": False, "required": ["items"],
            "properties": {"items": {"type": "array", "items": label_item}},
        },
    }


def ensure_runtime_dirs() -> None:
    for path in (SCHEMA_DIR, CACHE_DIR, CODEX_CWD):
        path.mkdir(parents=True, exist_ok=True)
    for name, schema in schema_documents().items():
        (SCHEMA_DIR / f"{name}.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def normalize_name(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


def sentence_count(text: str) -> int:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return 0
    return len([part for part in re.split(r"(?<=[.!?。])\s+", normalized) if part.strip()])


def validate_products_payload(payload: dict[str, Any], category: str,
                              used_names: set[str] | None = None) -> list[dict[str, Any]]:
    used = {normalize_name(name) for name in (used_names or set())}
    rows = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != PRODUCTS_PER_BATCH:
        actual = len(rows) if isinstance(rows, list) else 0
        raise BatchValidationError(f"products 배열은 {PRODUCTS_PER_BATCH}개여야 함(실제 {actual})", actual)

    batch_names: set[str] = set()
    sparse = 0
    normalized_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise BatchValidationError(f"{index}번 상품이 객체가 아님", len(rows))
        name, notes = row.get("name"), row.get("notes")
        features, colors = row.get("visible_features"), row.get("colors")
        if not isinstance(name, str) or not name.strip():
            raise BatchValidationError(f"{index}번 상품 name이 비어 있음", len(rows))
        if not isinstance(notes, str) or not notes.strip():
            raise BatchValidationError(f"{index}번 상품 notes가 비어 있음", len(rows))
        if not isinstance(features, list) or not 2 <= len(features) <= 5 or not all(isinstance(x, str) and x.strip() for x in features):
            raise BatchValidationError(f"{index}번 visible_features는 문자열 2~5개여야 함", len(rows))
        if not isinstance(colors, list) or not 1 <= len(colors) <= 3 or not all(isinstance(x, str) and x.strip() for x in colors):
            raise BatchValidationError(f"{index}번 colors는 문자열 1~3개여야 함", len(rows))
        count = sentence_count(notes)
        if not 1 <= count <= 5:
            raise BatchValidationError(f"{index}번 notes 문장 수가 1~5 범위 밖임({count})", len(rows))
        sparse += count == 1
        normalized = normalize_name(name)
        if normalized in used or normalized in batch_names:
            raise BatchValidationError(f"중복 상품명: {name}", len(rows))
        batch_names.add(normalized)
        normalized_rows.append({
            "category": category,
            "name": name.strip(),
            "notes": notes.strip(),
            "visible_features": [item.strip() for item in features],
            "colors": [item.strip() for item in colors],
        })
    if sparse != 2:
        raise BatchValidationError(f"빈약한 1문장 설명은 배치당 2개여야 함(실제 {sparse})", len(rows))
    return normalized_rows


def _prompt_key(command: str, prompt: str, attempt: int) -> str:
    digest = hashlib.sha256(f"{command}\0{attempt}\0{prompt}".encode("utf-8")).hexdigest()[:16]
    return f"{command}-{digest}"


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def call_codex(command: str, prompt: str, validator: Callable[[dict[str, Any]], Any],
               stats: RunStats, key_hint: str = "") -> tuple[Any | None, str | None]:
    """Call Codex with a schema, retry twice, and cache raw structured responses."""
    schema_path = SCHEMA_DIR / f"{command}.json"
    last_error = "실패 원인 미상"
    for attempt in range(1, MAX_RETRIES + 2):
        attempt_prompt = prompt
        if attempt > 1:
            attempt_prompt += (
                "\n\n이전 시도가 검증되지 않았습니다. 반드시 새 결과를 생성하고 다음 문제를 고치세요: "
                f"{last_error}"
            )
        cache_key = _prompt_key(command, attempt_prompt, attempt)
        if key_hint:
            cache_key = f"{key_hint}-{cache_key.rsplit('-', 1)[-1]}"
        cache_path = CACHE_DIR / command / f"{cache_key}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _read_cache(cache_path) if cache_path.exists() else None
        if payload is not None:
            stats.add("cache_hits")
        else:
            stats.add("calls")
            if attempt > 1:
                stats.add("retries")
            command_line = [
                "codex", "exec", "-s", "read-only", "--skip-git-repo-check",
                "--output-schema", str(schema_path), "-o", str(cache_path), attempt_prompt,
            ]
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command_line, cwd=CODEX_CWD, capture_output=True, text=True,
                    timeout=CODEX_TIMEOUT_SECONDS, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                stats.add("failures")
                last_error = f"codex exec 실행 실패: {exc}"
                print(f"[{command}] {key_hint} attempt {attempt}: {last_error}", flush=True)
                continue
            call_seconds = round(time.perf_counter() - started, 1)
            if completed.returncode != 0:
                stats.add("failures")
                last_error = f"종료 코드 {completed.returncode}: {completed.stderr[-600:].strip()}"
                print(f"[{command}] {key_hint} attempt {attempt}: {last_error} ({call_seconds}s)", flush=True)
                continue
            payload = _read_cache(cache_path)
            if payload is None:
                stats.add("failures")
                last_error = "-o 응답 파일이 없거나 JSON 객체가 아님"
                print(f"[{command}] {key_hint} attempt {attempt}: {last_error} ({call_seconds}s)", flush=True)
                continue
            print(f"[{command}] {key_hint} attempt {attempt}: codex exec 완료 ({call_seconds}s)", flush=True)
        try:
            return validator(payload), None
        except (BatchValidationError, KeyError, TypeError, ValueError) as exc:
            stats.add("failures")
            last_error = str(exc)
            discarded = exc.discarded if isinstance(exc, BatchValidationError) else 0
            stats.discard(discarded, last_error)
            print(f"[{command}] {key_hint} attempt {attempt}: 응답 검사 실패: {last_error}", flush=True)
    return None, last_error


def product_prompt(category: str, used_names: set[str], refresh_note: str = "") -> str:
    listed = ", ".join(sorted(used_names)) or "(없음)"
    prompt = f"""너는 한국 온라인 쇼핑몰의 가상 상품 데이터를 만든다. 분류 \"{category}\"의 서로 다른 가상 상품 10개를 만든다.
- name: 실제로 팔릴 법한 상품명. 실존 브랜드·상표·인물 이름은 쓰지 않는다. 이미 쓴 이름과 겹치지 않는다: {listed}
- notes: 판매자가 쓴 설명 2~5문장. 상품에 따라 숫자(용량·크기·무게·개수), 소재·원재료, 사용법, 관리·보관법을 섞는다. 10개 중 정확히 2개는 1문장짜리 빈약한 설명으로 쓴다.
- visible_features: 상품 사진에서 눈으로 보일 특징 2~5개(모양·부품·무늬·질감). 보이지 않는 성능·소재는 쓰지 않는다.
- colors: 사진에서 보일 색 1~3개.
각 상품은 서로 다른 유형이어야 한다. 이름과 설명에 실존 인물이나 실제 브랜드·상표를 넣지 말고, 가상의 상품만 작성한다."""
    if refresh_note:
        prompt += f"\n\n추가 요구: {refresh_note}"
    return prompt


def _product_batch(category_id: str, category: str, batch_number: int, used_names: set[str],
                   stats: RunStats, refresh_note: str = "") -> list[dict[str, Any]] | None:
    prompt = product_prompt(category, used_names, refresh_note)
    key_hint = f"{category_id}-{batch_number:02d}"
    rows, error = call_codex(
        "products", prompt,
        lambda payload: validate_products_payload(payload, category, used_names),
        stats, key_hint,
    )
    if rows is None:
        print(f"[products] {key_hint}: 건너뜀 — {error}", flush=True)
        return None
    return rows


def _generate_category(category_id: str, category: str, prior_names: set[str],
                       stats: RunStats) -> tuple[str, str, list[list[dict[str, Any]] | None]]:
    used = set(prior_names)
    batches: list[list[dict[str, Any]] | None] = []
    for batch_number in range(1, BATCHES_PER_CATEGORY + 1):
        batch = _product_batch(category_id, category, batch_number, used, stats)
        batches.append(batch)
        if batch:
            used.update(row["name"] for row in batch)
    return category_id, category, batches


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: JSON을 읽을 수 없음: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: JSON 객체가 아님")
        rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    temp.replace(path)


def _check_global_products(rows: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    ids: set[str] = set()
    names: set[str] = set()
    counts = Counter(row.get("category") for row in rows)
    for row in rows:
        ident = row.get("id")
        norm = normalize_name(str(row.get("name", "")))
        if ident in ids:
            problems.append(f"중복 id: {ident}")
        if norm in names:
            problems.append(f"중복 name: {row.get('name')}")
        ids.add(ident)
        names.add(norm)
        count = sentence_count(str(row.get("notes", "")))
        if not 1 <= count <= 5:
            problems.append(f"notes 문장 수 오류: {ident} ({count})")
    for category_id, category in CATEGORIES:
        expected_ids = {f"{category_id}-{index:03d}" for index in range(1, 61)}
        found_ids = {row.get("id") for row in rows if row.get("category") == category}
        if len(found_ids) != 60:
            problems.append(f"{category_id} 상품 수 {len(found_ids)}/60")
        if found_ids != expected_ids:
            problems.append(f"{category_id} id 연속성 오류")
    if len(rows) != 300:
        problems.append(f"전체 상품 수 {len(rows)}/300")
    return problems


def run_products(workers: int) -> int:
    stats = RunStats()
    existing = _load_jsonl(PRODUCTS_PATH)
    existing_problems = _check_global_products(existing) if existing else []
    if existing and not existing_problems:
        _print_product_summary(existing, stats, reused=True)
        return 0

    prior_names: set[str] = set()
    if existing:
        prior_names = {str(row.get("name", "")) for row in existing if row.get("name")}
        print(f"기존 products.jsonl 형식/개수 검증 실패 — 캐시에서 재생성합니다: {existing_problems[:3]}", flush=True)

    generated: dict[str, tuple[str, list[list[dict[str, Any]] | None]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate_category, code, label, prior_names, stats): code
            for code, label in CATEGORIES
        }
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                category_id, category, batches = future.result()
                generated[category_id] = (category, batches)
            except Exception as exc:  # keep other categories moving; final counts expose this failure
                generated[code] = (dict(CATEGORIES)[code], [None] * BATCHES_PER_CATEGORY)
                print(f"[products] {code}: 카테고리 생성 실패: {exc}", flush=True)

    accepted: list[dict[str, Any]] = []
    accepted_names = {normalize_name(name) for name in prior_names}
    accepted_names_by_text = set(prior_names)
    for category_id, category in CATEGORIES:
        batches = generated.get(category_id, (category, [None] * BATCHES_PER_CATEGORY))[1]
        for batch_number, batch in enumerate(batches, 1):
            if not batch:
                continue
            normalized = [normalize_name(row["name"]) for row in batch]
            if any(name in accepted_names for name in normalized):
                # A category ran concurrently with others. Replace a colliding batch
                # with a new one informed by all names accepted so far.
                avoid = set(accepted_names_by_text)
                avoid.update(row["name"] for row in batch)
                replacement = _product_batch(
                    category_id, category, batch_number, avoid, stats,
                    refresh_note="이번 배치의 기존 이름을 전부 버리고, 이미 사용한 이름과 겹치지 않는 새로운 상품 10개를 다시 만든다.",
                )
                if not replacement:
                    stats.discard(len(batch), "분류 간 상품명 중복 배치 재생성 실패")
                    continue
                stats.discard(len(batch), "분류 간 상품명 중복으로 배치 재생성")
                batch = replacement
                normalized = [normalize_name(row["name"]) for row in batch]
            if any(name in accepted_names for name in normalized):
                stats.discard(len(batch), "중복 상품명 재생성 후에도 충돌")
                continue
            for index, row in enumerate(batch):
                row["id"] = f"{category_id}-{(batch_number - 1) * PRODUCTS_PER_BATCH + index + 1:03d}"
                accepted.append(row)
                accepted_names.add(normalize_name(row["name"]))
                accepted_names_by_text.add(row["name"])

    # Stable order by requested category and id; write even on partial failure for inspection.
    order = {category: index for index, (_, category) in enumerate(CATEGORIES)}
    accepted.sort(key=lambda row: (order.get(row["category"], 999), row["id"]))
    _write_jsonl(PRODUCTS_PATH, accepted)
    problems = _check_global_products(accepted)
    _print_product_summary(accepted, stats, reused=False)
    if problems:
        print("검증 문제:", " | ".join(problems), flush=True)
        return 1
    return 0


def _print_product_summary(rows: list[dict[str, Any]], stats: RunStats, reused: bool) -> None:
    counts = Counter(row.get("category") for row in rows)
    sparse = sum(sentence_count(str(row.get("notes", ""))) == 1 for row in rows)
    denominator = len(rows)
    print("상품 수:", ", ".join(f"{code}={counts[label]}" for code, label in CATEGORIES), flush=True)
    print(f"총 {denominator}개, 고유 상품명 {len({normalize_name(str(r.get('name', ''))) for r in rows})}개", flush=True)
    print(f"빈약한 1문장 설명 {sparse}/{denominator} ({(100 * sparse / denominator) if denominator else 0:.1f}%)", flush=True)
    print(f"버림 {stats.discarded}개: {dict(stats.discard_reasons)}", flush=True)
    print(f"codex exec 호출 {stats.calls}, 실패 {stats.failures}, 재시도 {stats.retries}, 캐시 재사용 {stats.cache_hits}, 경과 {stats.elapsed}s", flush=True)
    if reused:
        print(f"기존 검증 완료 데이터 재사용: {PRODUCTS_PATH}", flush=True)
    else:
        print(f"저장: {PRODUCTS_PATH}", flush=True)


def _basis_for_product(product: dict[str, Any]) -> str:
    page = {
        "product": {"name": product["name"], "notes": product["notes"]},
        "analysis": {
            "visible_features": product.get("visible_features", []),
            "colors": product.get("colors", []),
            "photo_captions": [],
        },
    }
    return basis_of(page, [])


def _sentence_row_key(row: dict[str, Any]) -> str:
    return str(row.get("id") or "")


def _compact_space(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _label_api() -> tuple[Callable[..., Any], Callable[..., Any]]:
    # Keep the data-builder module out of products/inject-only imports, while
    # using its exact acceptance and flagged rules for this task.
    from scripts.finetune.judge_data import flagged, validate_label

    return validate_label, flagged


def _label_rejection_reason(text: str, label: dict[str, Any], injected: str | None = None) -> str:
    phrases = label.get("phrases") if isinstance(label, dict) else None
    if not isinstance(phrases, list) or not phrases:
        return "phrases 없음"
    from pagegen.page import is_sourced

    seen: set[str] = set()
    for phrase in phrases:
        if not isinstance(phrase, dict):
            return "phrase 형식 오류"
        if phrase.get("type") not in ("fact", "mood"):
            return "type 오류"
        if not isinstance(phrase.get("supported"), bool):
            return "supported 형식 오류"
        raw = str(phrase.get("phrase") or "")
        if not raw or not is_sourced(raw, text):
            return "phrase가 원문에서 확인되지 않음"
        if raw in seen:
            return "중복 phrase"
        seen.add(raw)
    if injected and not any(
        phrase["type"] == "fact" and phrase["supported"] is False
        and (is_sourced(injected, phrase["phrase"]) or is_sourced(phrase["phrase"], injected))
        for phrase in phrases
    ):
        return "주입 문장이 false fact로 표시되지 않음"
    return "judge_data.validate_label 거부(세부 원인 불명)"


def run_inject(workers: int, per_product: int) -> int:
    products = {row["id"]: row for row in _load_jsonl(PRODUCTS_PATH)}
    sentences = _load_jsonl(SENTENCES_PATH)
    labels = {row.get("id"): row for row in _load_jsonl(LABELS_PATH)}
    existing_injected = _load_jsonl(INJECTED_PATH)
    existing_injected_ids = {row.get("id") for row in existing_injected}
    validate_label, flagged_label = _label_api()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sentences:
        label = labels.get(row.get("id"))
        valid = validate_label(str(row.get("text", "")), label or {}) if label else None
        if (row.get("product_id") in products and row.get("source") in {"9b-t0", "9b-t07"}
                and valid is not None and not flagged_label(valid)
                and f"{_sentence_row_key(row)}-inj" not in existing_injected_ids):
            grouped[row["product_id"]].append(row)
    stats = RunStats()
    output: list[dict[str, Any]] = []
    tasks = []
    shortages: dict[str, int] = {}
    for product_id, product in products.items():
        already = sum(1 for row in existing_injected if row.get("product_id") == product_id)
        needed = max(0, per_product - already)
        rows = grouped.get(product_id, [])
        selected = rows[:needed]
        if len(selected) < needed:
            shortages[product_id] = needed - len(selected)
        if not selected:
            continue
        numbered = "\n".join(f"{index}. {row['text']}" for index, row in enumerate(selected, 1))
        prompt = (
            "아래 근거와 문장들이 있다. 문장마다, 근거에 없는 제품 사실 하나(기능, 성능, 소재, 인증, "
            "사용·관리 방법, 효능 중 하나)를 자연스럽게 끼워 넣은 새 문장을 만든다. 끼워 넣은 부분은 "
            "injected_phrase에 원문 그대로 적는다. 분위기·감정 표현은 사실이 아니므로 끼워 넣지 않는다. "
            "숫자는 쓰지 않는다(숫자는 다른 검사가 막는다).\n근거:\n"
            f"{_basis_for_product(product)}\n문장:\n{numbered}"
        )
        by_n = {index: row for index, row in enumerate(selected, 1)}

        def validator(payload: dict[str, Any], by_n=by_n, product_id=product_id) -> list[dict[str, Any]]:
            items = payload.get("items")
            if not isinstance(items, list) or len(items) != len(by_n):
                raise BatchValidationError(
                    f"items 개수 {len(items) if isinstance(items, list) else 0}/{len(by_n)}",
                    abs(len(items) - len(by_n)) if isinstance(items, list) else len(by_n),
                )
            result, seen = [], set()
            for item in items:
                n = item.get("n")
                if n not in by_n or n in seen:
                    raise BatchValidationError(f"잘못되거나 중복된 문장 번호: {n}")
                seen.add(n)
                text, phrase = item.get("text"), item.get("injected_phrase")
                if not isinstance(text, str) or not isinstance(phrase, str) or not phrase.strip():
                    raise BatchValidationError(f"{n}번 변형 문장 또는 injected_phrase가 비어 있음")
                if _compact_space(phrase) not in _compact_space(text):
                    raise BatchValidationError(f"{n}번 injected_phrase가 문장에 없음")
                if re.search(r"\d", text):
                    raise BatchValidationError(f"{n}번 변형 문장에 숫자가 있음")
                source = by_n[n]
                result.append({
                    "id": f"{_sentence_row_key(source)}-inj", "product_id": product_id,
                    "text": text, "source": "injected", "injected_phrase": phrase,
                })
            return result

        tasks.append((product_id, prompt, validator))

    def execute(task: tuple[str, str, Callable[[dict[str, Any]], Any]]) -> list[dict[str, Any]]:
        product_id, prompt, validator = task
        result, _ = call_codex("inject", prompt, validator, stats, product_id)
        return result or []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(execute, tasks):
            output.extend(result)
    merged = {row.get("id"): row for row in existing_injected}
    merged.update({row.get("id"): row for row in output})
    _write_jsonl(INJECTED_PATH, list(merged.values()))
    print(f"inject 신규 {len(output)}개, 누적 {len(merged)}개, 상품별 목표 부족 {len(shortages)}개({sum(shortages.values())}문장), "
          f"호출 {stats.calls}, 실패 {stats.failures}, 재시도 {stats.retries}, 캐시 재사용 {stats.cache_hits}, 경과 {stats.elapsed}s", flush=True)
    if shortages:
        print("주입 가능 문장 부족 상품:", ", ".join(f"{pid}:{count}" for pid, count in sorted(shortages.items())), flush=True)
    return 0 if stats.failures == 0 else 1


def run_label(workers: int) -> int:
    products = {row["id"]: row for row in _load_jsonl(PRODUCTS_PATH)}
    original_sentences = _load_jsonl(SENTENCES_PATH)
    injected_sentences = _load_jsonl(INJECTED_PATH)
    validate_label, _ = _label_api()
    existing_rows = _load_jsonl(LABELS_PATH)
    # every sentence is a target; ones with a label that still passes the checks are skipped,
    # so deleting labels from labels.jsonl is how a set of sentences gets labeled again
    sentences = original_sentences + injected_sentences
    sentence_by_id = {row.get("id"): row for row in sentences}
    accepted_existing: dict[str, dict[str, Any]] = {}
    rejected_existing = Counter()
    for label in existing_rows:
        source = sentence_by_id.get(label.get("id"))
        validated = validate_label(str(source.get("text", "")), label, source.get("injected_phrase")) if source else None
        if validated is None:
            rejected_existing["기존 라벨 기계 검사 탈락"] += 1
        else:
            accepted_existing[label["id"]] = {"id": label["id"], **validated, "teacher": label.get("teacher", "codex")}
    todo = [row for row in sentences if row.get("product_id") in products and row.get("id") not in accepted_existing]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in todo:
        grouped[row["product_id"]].append(row)
    stats = RunStats()
    tasks = []
    for product_id, rows in grouped.items():
        product = products[product_id]
        for offset in range(0, len(rows), 20):
            chunk = rows[offset:offset + 20]
            numbered = "\n".join(f"{index}. {row['text']}" for index, row in enumerate(chunk, 1))
            prompt = f"""{prompts.JUDGE_SYSTEM}

정답 매기기 규칙(교사용):
- 문장마다 짧은 구절로 나누고 모든 구절을 빠짐없이 분류한다. phrase는 문장에 있는 글자 그대로 옮긴다(바꿔 쓰지 않는다).
- 분위기·감정·장면·추천·주관적 꾸밈말은 mood(supported=true).
- 제품의 기능·성능·소재·크기·인증·호환·사용 가능 여부·사용/관리 지시·제조 방식·품질·내구성을 단정하면 fact. 근거 텍스트나 사진에서 보이는 것에 같은 뜻이 있으면 supported=true, 없거나 추측이 필요하면 false. 애매하면 false.
- 근거 텍스트에 있는 판매자 주장은 근거로 본다.
- 내용 없는 제목·안내 문구("사용 전 확인해주세요", "보관 방법", "관리 및 주의 사항")는 제품 사실을 주장하지 않으므로 mood다.
- 근거에 있는 사실을 다른 말로 바꿔 말한 구절은 supported=true다. 예) 근거 "한 손에 쥐기 좋은 크기" → "한 손으로 잡기 편해요", 근거 "2인분" → "두 사람이 먹기 좋은 양".
- 근거의 수치를 주관적으로 요약한 꾸밈말은 mood다. 예) 근거 "높이 120cm" → "넉넉한 높이로".
- "간편하게", "편하게", "손쉽게" 같은 편리함 부사는 꾸밈말(mood)이다. 단 "세척이 편해요", "손이 덜 가는"처럼 제품 특성을 단정하면 fact다.
- 입었을 때의 편안함·착용감·핏은 촉감처럼 제품 특성이므로 fact다.
- 근거보다 강하게 말하면 supported=false다. 예) 근거 "냄새를 줄여 주는" → "냄새가 전혀 나지 않아요".

{_basis_for_product(product)}
문장:
{numbered}"""
            by_n = {index: row for index, row in enumerate(chunk, 1)}

            def validator(payload: dict[str, Any], by_n=by_n) -> list[dict[str, Any]]:
                items = payload.get("items")
                if not isinstance(items, list) or len(items) != len(by_n):
                    actual = len(items) if isinstance(items, list) else 0
                    raise BatchValidationError(f"items 개수 {actual}/{len(by_n)}", abs(actual - len(by_n)))
                output, seen, rejected = [], set(), Counter()
                for item in items:
                    n = item.get("n")
                    if n not in by_n or n in seen:
                        rejected["잘못되거나 중복된 문장 번호"] += 1
                        continue
                    seen.add(n)
                    source = by_n[n]
                    raw_label = {"phrases": item.get("phrases")}
                    injected = source.get("injected_phrase")
                    validated = validate_label(source["text"], raw_label, injected)
                    if validated is None:
                        rejected[_label_rejection_reason(source["text"], raw_label, injected)] += 1
                    else:
                        output.append({"id": source["id"], **validated, "teacher": "codex"})
                missing = len(by_n) - len(seen)
                if missing:
                    rejected["응답에서 문장 누락"] += missing
                if rejected:
                    reason = ", ".join(f"{key}:{count}" for key, count in sorted(rejected.items()))
                    raise BatchValidationError(f"기계 검사 탈락({reason})", sum(rejected.values()))
                return output

            tasks.append((product_id, offset // 20, prompt, validator))

    def execute(task: tuple[str, int, str, Callable[[dict[str, Any]], Any]]) -> list[dict[str, Any]]:
        product_id, group_number, prompt, validator = task
        result, error = call_codex("label", prompt, validator, stats, f"{product_id}-{group_number:02d}")
        if result is None:
            print(f"[label] {product_id}-{group_number:02d}: 건너뜀 — {error}", flush=True)
        return result or []

    output = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(execute, tasks):
            output.extend(result)
    merged = dict(accepted_existing)
    merged.update({row["id"]: row for row in output})
    _write_jsonl(LABELS_PATH, list(merged.values()))
    target_ids = {row.get("id") for row in sentences if row.get("product_id") in products}
    missing = len(target_ids - set(merged))
    print(f"label: 신규 통과 {len(output)}개, 기존 통과 재사용 {len(accepted_existing)}개, "
          f"누적 저장 {len(merged)}개, 대상 미통과/누락 {missing}개, 기존 라벨 탈락 {dict(rejected_existing)}, "
          f"검증 실패 이유 {dict(stats.discard_reasons)}, 호출 {stats.calls}, 실패 {stats.failures}, "
          f"재시도 {stats.retries}, 캐시 재사용 {stats.cache_hits}, 경과 {stats.elapsed}s", flush=True)
    return 0 if stats.failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("products", "가상 상품 300개 생성"),
        ("inject", "근거 있는 9B 문장에 근거 없는 사실을 끼운 변형 생성"),
        ("label", "문장별 주장 정답 생성"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--workers", type=int, default=4, help="동시 Codex 호출 수 (기본: 4)")
        if name == "inject":
            sub.add_argument("--per-product", type=int, choices=(2, 3, 4), default=2,
                             help="상품별 총 주입 문장 수 (기본: 2)")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers는 1 이상이어야 합니다")
    ensure_runtime_dirs()
    if args.command == "products":
        return run_products(args.workers)
    if args.command == "inject":
        return run_inject(args.workers, args.per_product)
    return run_label(args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
