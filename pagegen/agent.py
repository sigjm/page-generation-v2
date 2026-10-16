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
