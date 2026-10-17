"""Chat turns (tool calls, spec §4.2) and the first draft (fixed order, spec §4.1). `llm` is injectable for tests."""
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

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


class ChatState(TypedDict):
    messages: list[dict]
    log: list[dict]
    changed: bool
    reply: str | None
    rounds: int


def build_chat_graph(folder: Path, page: dict, corpus: str, basis: str, llm):
    """model ⇄ tools until the model answers in text, asks the user, or uses up MAX_ROUNDS (spec §4.2)."""
    def model(state: ChatState) -> dict:
        message = llm.chat(state["messages"], tools=prompts.TOOLS, thinking=True, max_tokens=1024)
        calls = message.get("tool_calls") or []
        if not calls:
            return {"reply": message.get("content") or ("반영했어요." if state["changed"] else "무엇을 바꿀까요?")}
        return {"messages": [*state["messages"], {"role": "assistant", "content": message.get("content") or "",
                                                  "tool_calls": calls}]}

    def tools(state: ChatState) -> dict:
        messages, log, changed = list(state["messages"]), list(state["log"]), state["changed"]
        for call in messages[-1]["tool_calls"]:
            name, args = call.get("function", {}).get("name", ""), {}
            try:
                args = _args(call)
                if name == "ask_user":
                    log.append({"name": name, "args": args, "ok": True, "result": "질문"})
                    return {"messages": messages, "log": log, "changed": changed,
                            "reply": args.get("question") or "조금 더 알려 주세요."}
                result, ok = _run_tool(folder, page, name, args, corpus, basis, llm), True
            except pg.ToolError as e:
                result, ok = str(e), False
            log.append({"name": name, "args": args, "ok": ok, "result": result})
            changed |= ok and name in MUTATING
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        rounds = state["rounds"] + 1
        return {"messages": messages, "log": log, "changed": changed, "rounds": rounds,
                "reply": "여기까지 반영했어요." if rounds >= MAX_ROUNDS else None}

    graph = StateGraph(ChatState)
    graph.add_node("model", model)
    graph.add_node("tools", tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", lambda s: END if s["reply"] is not None else "tools")
    graph.add_conditional_edges("tools", lambda s: END if s["reply"] is not None else "model")
    # ponytail: no checkpointer; store.py already persists page/chat per turn. Add SqliteSaver if turns must resume
    return graph.compile()


def chat_turn(folder: Path, page: dict, chat: list[dict], text: str, llm=default_llm) -> tuple[dict, bool]:
    """One user message (spec §4.2). Mutates page and chat; returns (assistant message, page changed).
    A text-model failure raises LLMError; the caller then leaves page.json as it was."""
    history = [{"role": m["role"], "content": m["text"]} for m in chat[-HISTORY:]]
    chat.append({"role": "user", "text": text})
    corpus = pg.corpus_of(page, chat)
    basis = basis_of(page, chat)
    messages = [{"role": "system", "content": prompts.chat_system(page)}, *history, {"role": "user", "content": text}]
    graph = build_chat_graph(folder, page, corpus, basis, llm)
    state = graph.invoke({"messages": messages, "log": [], "changed": False, "reply": None, "rounds": 0},
                         {"recursion_limit": 4 * MAX_ROUNDS + 4})
    answer = {"role": "assistant", "text": state["reply"], "tools": state["log"]}
    chat.append(answer)
    return answer, state["changed"]


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


DRAFT_STEPS = ("analyze", "caption", "write_copy", "assemble", "check", "edit_scene", "first_message")


class DraftState(TypedDict, total=False):
    page: dict
    corpus: str
    seller: str
    copy: dict
    sections: list[dict]
    instruction: str
    message: dict


def build_draft_graph(folder: Path, llm):
    """The fixed-order first draft (spec §4.1) as a LangGraph pipeline; each node is a step make_draft used to run inline."""
    def analyze(s: DraftState) -> dict:
        page = s["page"]
        images = [llm.image_part(llm.model_jpeg(folder / p["file"])) for p in page["photos"][:4]]
        raw = llm.chat_json([{"role": "system", "content": prompts.ANALYZE_SYSTEM},
                             {"role": "user", "content": [*images, {"type": "text", "text": s["seller"]}]}],
                            prompts.ANALYZE_SCHEMA)
        page["analysis"] = normalize_analysis(raw, s["corpus"])
        return {"page": page}

    def caption(s: DraftState) -> dict:
        page = s["page"]
        # captions[i] belongs to photos[i]; p1 is covered by the analysis
        page["analysis"]["photo_captions"] = ["", *(_caption(folder, p, llm) for p in page["photos"][1:3])]
        return {"page": page}

    def write_copy(s: DraftState) -> dict:
        page = s["page"]
        captions = page["analysis"]["photo_captions"]
        split_lines = [f"사진 {i + 2}: {captions[i + 1]}" for i in range(min(2, len(page["photos"]) - 1))
                       if i + 1 < len(captions) and captions[i + 1]]
        hint = ("details를 쓸 사진 설명:\n" + "\n".join(split_lines)) if split_lines else "사진 설명 없음: details는 빈 목록."
        copy = llm.chat_json([{"role": "system", "content": prompts.COPY_SYSTEM},
                              {"role": "user", "content": f"{s['seller']}\n사진 분석: {json.dumps(page['analysis'], ensure_ascii=False)}\n{hint}"}],
                             prompts.COPY_SCHEMA)
        return {"copy": copy}

    def assemble(s: DraftState) -> dict:
        page = s["page"]
        sections, instruction = build_sections(s["copy"], page["analysis"], page["product"]["name"], page["photos"])
        return {"sections": _settle(sections), "instruction": instruction}

    def check(s: DraftState) -> dict:
        page, sections = s["page"], s["sections"]
        basis, tally = basis_of(page, []), {"judged": 0, "failed": 0}
        for section in sections:
            if section["kind"] not in ("spec_table", "gallery"):  # facts are spec-checked; the gallery title is fixed
                _clean(section, s["corpus"], basis, page["product"]["name"], llm, tally)
        if tally["judged"] and tally["failed"] == tally["judged"]:
            raise LLMError("주장 검사를 하지 못해 초안을 만들지 않았습니다. 모델 서버 상태를 확인해 주세요.")
        page["sections"] = [{"id": f"s{i}", **sec} for i, sec in enumerate(_settle(sections), 1)]
        return {"page": page}

    def edit_scene(s: DraftState) -> dict:
        page = s["page"]
        scene = next((sec for sec in page["sections"] if sec["kind"] == "scene"), None)
        if scene:
            try:
                edit_photo(folder, page, scene["id"], s["instruction"], llm)
            except pg.ToolError:
                pass  # keep the original photo (spec §4.1-5)
        return {"page": page}

    def first_message(s: DraftState) -> dict:
        questions = s["page"]["analysis"]["questions"]
        text = "초안을 만들었어요. 오른쪽 미리보기를 보고 고칠 점을 말씀해 주세요."
        if questions:
            text += "\n더 채우려면 알려 주세요:\n" + "\n".join(f"- {q}" for q in questions)
        return {"message": {"role": "assistant", "text": text, "tools": []}}

    graph = StateGraph(DraftState)
    for name, step in zip(DRAFT_STEPS, (analyze, caption, write_copy, assemble, check, edit_scene, first_message)):
        graph.add_node(name, step)
    for a, b in zip((START, *DRAFT_STEPS), (*DRAFT_STEPS, END)):
        graph.add_edge(a, b)
    return graph.compile()


def make_draft(folder: Path, pid: str, product: dict, photos: list[dict], llm=default_llm) -> tuple[dict, dict]:
    """Spec §4.1. Returns (page, first assistant message). Raises LLMError and then no draft exists."""
    page = {"id": pid, "version": 1, "product": product, "analysis": {}, "photos": photos, "sections": []}
    seller = f"상품명: {product['name']}\n판매자 설명: {product['notes'] or '(없음)'}"
    state = build_draft_graph(folder, llm).invoke({"page": page, "corpus": pg.corpus_of(page, []), "seller": seller})
    return state["page"], state["message"]
