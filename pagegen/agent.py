"""Chat turns (tool calls, spec §4.2) and the first draft (fixed order, spec §4.1). `llm` is injectable for tests."""
import json
import re
from collections.abc import Callable
from pathlib import Path

from . import llm as default_llm
from . import page as pg
from . import prompts
from .llm import LLMError

MAX_ROUNDS = 4
HISTORY = 12  # the last 6 turns, user + assistant
MUTATING = {"update_text", "edit_photo", "use_photo", "add_section", "remove_section", "move_section", "set_variant"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def basis_of(page: dict, chat: list[dict]) -> str:
    analysis = page["analysis"]
    seen = analysis["visible_features"] + analysis["colors"] + [c for c in analysis.get("photo_captions", []) if c]
    return f"근거 텍스트: {pg.corpus_of(page, chat)}\n사진에서 보이는 것: {', '.join(seen) or '(없음)'}"


def judge(llm, text: str, basis: str) -> list[str]:
    """Phrases of `text` asserting product facts missing from `basis` (spec §5). Raises LLMError: fail-closed."""
    if not text.strip():
        return []
    data = llm.chat_json([{"role": "system", "content": prompts.JUDGE_SYSTEM},
                          {"role": "user", "content": f"{basis}\n새 문구: {text}"}],
                         prompts.JUDGE_SCHEMA, max_tokens=800)
    return [str(p.get("phrase") or text) for p in data.get("phrases", [])
            if p.get("type") != "mood" and p.get("supported") is not True]


def _sentences(line: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(line.strip()) if s]


def unsupported(llm, text: str, basis: str) -> list[str]:
    """Judge one sentence per call: the 9B misses claims buried in longer text (seen in real drafts)."""
    return [f for line in text.splitlines() for s in _sentences(line) for f in judge(llm, s, basis)]


def _verifier(llm, basis: str, old: str = "") -> Callable[[str], None]:
    """Claim check for new text. Sentences already on the page are not judged again: the judge can flip on
    them as the basis grows, and the model then silently drops the seller's own rows to get past it."""
    seen = {s for line in old.splitlines() for s in _sentences(line)}

    def verify(text: str) -> None:
        new = "\n".join(s for line in text.splitlines() for s in _sentences(line) if s not in seen)
        try:
            flagged = unsupported(llm, new, basis)
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


def _run_tool(folder: Path, page: dict, name: str, args: dict, corpus: str, basis: str, llm) -> str:
    a = {key: str(value) for key, value in args.items()}
    try:
        match name:
            case "update_text":
                old = pg.find_section(page, a["section_id"]).get(a["field"], "")
                return pg.update_text(page, a["section_id"], a["field"], a["text"], corpus,
                                      _verifier(llm, basis, str(old)))
            case "add_section":
                return pg.add_section(page, a["kind"], a.get("after_section_id", ""), a.get("headline", ""),
                                      a.get("body", ""), corpus, _verifier(llm, basis))
            case "remove_section":
                return pg.remove_section(page, a["section_id"])
            case "move_section":
                return pg.move_section(page, a["section_id"], a.get("after_section_id", ""))
            case "use_photo":
                return pg.use_photo(page, a["section_id"], a["photo_id"])
            case "set_variant":
                return pg.set_variant(page, a["section_id"], a["variant"])
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
    basis = basis_of(page, chat)
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
                result, ok = _run_tool(folder, page, name, args, corpus, basis, llm), True
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


def normalize_analysis(raw: dict, corpus: str) -> dict:
    """Spec §3: every key present; facts only when the value appears in the seller's text."""
    def strings(key: str, limit: int) -> list[str]:
        items = raw.get(key)
        return [str(x).strip() for x in items if str(x).strip()][:limit] if isinstance(items, list) else []

    facts = []
    for fact in raw.get("facts") or []:
        if isinstance(fact, dict):
            key, value = str(fact.get("key") or "").strip(), str(fact.get("value") or "").strip()
            row = f"{key}: {value}"
            if (key and value and pg.is_sourced(value, corpus) and not pg.unsupported_numbers(row, corpus)
                    and not pg.unsupported_superlatives(row, corpus) and not pg.unsupported_hanja(row, corpus)):
                facts.append({"key": key, "value": value})
    return {"category": str(raw.get("category") or "").strip(), "visible_features": strings("visible_features", 5),
            "colors": strings("colors", 5), "facts": facts, "questions": strings("questions", 3)}


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
        {"kind": "banner", "variant": "light", "body": "",
         "headline": "" if get("banner", "headline") == (get("hero", "headline") or name) else get("banner", "headline")},
        {"kind": "points", "variant": "cards", "headline": get("points", "headline") or "이래서 좋아요",
         "body": "\n".join(f"{str(i.get('title') or '').strip()}: {str(i['text']).strip()}" for i in items)},
    ]
    # details follow the captions the copy model was given, which skip photos whose caption failed:
    # pair them with captioned photos only, or a failed p2 caption would put p3's copy on p2
    captions = analysis.get("photo_captions", [])
    captioned = [p for i, p in enumerate(uploads[1:3], 1) if i < len(captions) and captions[i]]
    detail_of = dict(zip(captioned, details))
    for i, photo in enumerate(uploads[1:3]):
        detail = detail_of.get(photo, {})
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


def _clean(section: dict, corpus: str, basis: str, name: str, llm, tally: dict) -> None:
    """Spec §4.1-6 for one model-written section: drop sentences, or replace the headline, failing either check.
    A sentence the judge cannot answer (the 9B sometimes loops until max_tokens) is dropped, not fatal."""
    def passes(text: str) -> bool:
        if (pg.unsupported_numbers(text, corpus) or pg.unsupported_superlatives(text, corpus)
                or pg.unsupported_hanja(text, corpus)):
            return False
        if not text.strip():
            return True
        tally["judged"] += 1
        try:
            return not judge(llm, text, basis)
        except LLMError:
            tally["failed"] += 1
            return False  # unjudged text never reaches the page

    if not passes(section["headline"]):
        section["headline"] = "" if section["kind"] in ("banner", "split") else name  # don't repeat the hero title
    lines = []
    for line in section["body"].splitlines():
        sentences = _sentences(line)
        kept = [s for s in sentences if passes(s)]
        if section["kind"] == "points" and len(kept) < len(sentences):
            continue  # a point cut in half loses its title or its meaning: drop the whole point
        if kept:
            lines.append(" ".join(kept))
    section["body"] = "\n".join(lines)


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


def _caption(folder: Path, photo: dict, llm) -> str:
    """What one split photo shows. A failed call returns "": the split gets no copy and its photo goes to the gallery."""
    try:
        data = llm.chat_json([{"role": "system", "content": prompts.CAPTION_SYSTEM},
                              {"role": "user", "content": [llm.image_part(llm.model_jpeg(folder / photo["file"])),
                                                           {"type": "text", "text": "이 사진에서 보이는 것을 한 문장으로."}]}],
                             prompts.CAPTION_SCHEMA, max_tokens=200)
    except LLMError:
        return ""
    return str(data.get("caption") or "").strip()


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
    captions = [""]  # p1 is covered by the analysis; captions[i] belongs to photos[i]
    for photo in photos[1:3]:
        captions.append(_caption(folder, photo, llm))
    page["analysis"]["photo_captions"] = captions
    split_lines = [f"사진 {i + 2}: {captions[i + 1]}" for i in range(min(2, len(photos) - 1))
                   if i + 1 < len(captions) and captions[i + 1]]
    detail_hint = ("details를 쓸 사진 설명:\n" + "\n".join(split_lines)) if split_lines else "사진 설명 없음: details는 빈 목록."
    copy = llm.chat_json([{"role": "system", "content": prompts.COPY_SYSTEM},
                          {"role": "user", "content": f"{seller}\n사진 분석: {json.dumps(page['analysis'], ensure_ascii=False)}\n{detail_hint}"}],
                         prompts.COPY_SCHEMA)
    sections, instruction = build_sections(copy, page["analysis"], product["name"], photos)
    sections = _settle(sections)
    basis = basis_of(page, [])
    tally = {"judged": 0, "failed": 0}
    for section in sections:
        if section["kind"] not in ("spec_table", "gallery"):  # facts are spec-checked; the gallery title is fixed
            _clean(section, corpus, basis, product["name"], llm, tally)
    if tally["judged"] and tally["failed"] == tally["judged"]:
        raise LLMError("주장 검사를 하지 못해 초안을 만들지 않았습니다. 모델 서버 상태를 확인해 주세요.")
    page["sections"] = [{"id": f"s{i}", **s} for i, s in enumerate(_settle(sections), 1)]
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
