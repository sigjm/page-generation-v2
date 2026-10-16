# LangChain·LangGraph·LangSmith 적용 구현 계획 (0단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모델 호출을 LangChain `ChatOpenAI`로, 채팅 턴과 첫 초안을 LangGraph 그래프로 옮기고, 개발·학습 데이터만 LangSmith로 추적·평가할 수 있게 한다. 사용자에게 보이는 동작은 바꾸지 않는다.

**Architecture:** `llm.chat`·`llm.chat_json`의 시그니처와 반환 모양을 그대로 두고 내부만 LangChain으로 바꾼다(에이전트 테스트의 `FakeLLM`이 계속 이 자리를 대신한다). `agent.chat_turn`·`agent.make_draft`는 같은 시그니처로 내부에서 LangGraph `StateGraph`를 만들어 돈다. 체크포인터는 쓰지 않는다.

**Tech Stack:** + `langchain-core>=1.6`, `langchain-openai>=1.7`, `langgraph>=1.2`, `langsmith>=0.14`.

**Spec:** `docs/superpowers/specs/2026-10-16-langchain-adoption-design.md`

## Global Constraints

- `tests/test_agent.py`, `tests/test_server.py`는 내용을 고치지 않고 통과해야 한다(새 테스트 추가는 된다).
- `llm.chat(messages, *, tools, schema, thinking, max_tokens, temperature)`의 반환은 `{"content": str, "tool_calls": [OpenAI 형식, arguments는 JSON 문자열]}`.
- 오류 메시지 형식: `모델 서버 오류: {상태코드} {본문 앞 200자}` / `모델 서버 오류: {예외}`.
- 사진 편집(`edit_image`)은 urllib `_post` 그대로.
- 앱 서버는 `PAGEGEN_TRACE=1`이 아니면 LangSmith 추적을 끈다. 키는 `.env`(git 제외)에서만 읽는다.

## Review Focus

1. **인자 JSON이 깨진 도구 호출** → LangChain이 `invalid_tool_calls`로 빼는데, 원문 그대로 `tool_calls`에 되돌려 에이전트가 "인자 JSON" 오류를 모델에 돌려줘야 한다. (Task 2: `test_chat_keeps_invalid_tool_calls_raw`)
2. **사용자가 셸에 `LANGSMITH_TRACING=true`를 켜 둔 채 앱 서버를 띄움** → 서버는 그래도 끈다. (Task 1: `test_server_turns_tracing_off_by_default`)
3. **`.env`에 빈 값(`LANGSMITH_API_KEY=`)이나 주석** → 무시한다. 환경 변수가 이미 있으면 `.env`가 덮어쓰지 않는다. (Task 1: `test_dotenv_rules`)
4. **노드 안에서 `LLMError`** → 그래프 밖으로 그대로 나와 서버가 502를 내야 한다. (기존 `test_text_model_error_propagates`, `test_draft_judge_failure_means_no_draft`)
5. **채팅 4라운드 상한** → 모델 호출이 정확히 4번이어야 한다. (기존 `test_round_cap`)

---

### Task 0: 기준선 측정 (Claude)

- [ ] 실행 중인 서버(:8000, 지금 코드)로 사진 2장 초안을 만들어 시간을 잰다:
  `curl -s -o /dev/null -w "%{time_total}\n" -F "photos=@<p1>" -F "photos=@<p2>" -F "name=…" -F "notes=…" localhost:8000/projects` (CC0 사진 2장, 같은 입력을 Task 5에서 다시 쓴다). 값을 ledger에 적는다.
- [ ] `uv run python scripts/eval_tools.py` 결과(지금 12/13·8/8)를 ledger에 적는다.

---

### Task 1: 의존성, `.env`, 추적 스위치 (Claude)

**Files:** Modify `pyproject.toml`, `pagegen/__init__.py`, `pagegen/server.py`; Create `tests/test_tracing.py`

**Interfaces — Produces:** `pagegen.load_dotenv(path=Path(".env"))`, `pagegen.enable_dev_tracing(project="pagegen-finetune") -> bool`, 서버 모듈 로드 시 추적 강제 끔.

- [ ] **Step 1: 의존성** — `pyproject.toml`의 dependencies에 `"langchain-core>=1.6"`, `"langchain-openai>=1.7"`, `"langgraph>=1.2"`, `"langsmith>=0.14"`를 더하고 `uv sync`.
- [ ] **Step 2: 실패하는 테스트** (`tests/test_tracing.py`)

```python
import importlib
import os

import pagegen


def test_dotenv_rules(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nLANGSMITH_API_KEY=\nLANGSMITH_PROJECT=from-file\nPAGEGEN_X=1\n", "utf-8")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_PROJECT", "from-shell")
    monkeypatch.delenv("PAGEGEN_X", raising=False)
    pagegen.load_dotenv(env)
    assert "LANGSMITH_API_KEY" not in os.environ  # empty values are ignored
    assert os.environ["LANGSMITH_PROJECT"] == "from-shell"  # the shell wins
    assert os.environ["PAGEGEN_X"] == "1"


def test_dev_tracing_needs_a_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert pagegen.enable_dev_tracing() is False and "LANGSMITH_TRACING" not in os.environ
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert pagegen.enable_dev_tracing("pagegen-finetune") is True
    assert os.environ["LANGSMITH_TRACING"] == "true" and os.environ["LANGSMITH_PROJECT"] == "pagegen-finetune"


def test_server_turns_tracing_off_by_default(monkeypatch):
    from pagegen import server
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("PAGEGEN_TRACE", raising=False)
    importlib.reload(server)
    assert os.environ["LANGSMITH_TRACING"] == "false"
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("PAGEGEN_TRACE", "1")
    importlib.reload(server)
    assert os.environ["LANGSMITH_TRACING"] == "true"
```

- [ ] **Step 3: 실패 확인** — `uv run pytest tests/test_tracing.py -q` → AttributeError
- [ ] **Step 4: 구현**

`pagegen/__init__.py`:

```python
"""pagegen. Reads KEY=VALUE lines from ./.env (git-ignored) into the environment; the shell always wins."""
import os
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text("utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip())


def enable_dev_tracing(project: str = "pagegen-finetune") -> bool:
    """LangSmith tracing for scripts (dev/fine-tuning data only, spec §4). Needs LANGSMITH_API_KEY."""
    if not os.environ.get("LANGSMITH_API_KEY"):
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", project)
    return True


load_dotenv()
```

`pagegen/server.py` — import 줄 아래:

```python
# Real product notes and copy never leave the Mac unless the user opts in (spec §4).
if os.environ.get("PAGEGEN_TRACE") != "1":
    os.environ["LANGSMITH_TRACING"] = os.environ["LANGCHAIN_TRACING_V2"] = "false"
```

(`import os` 추가.)

- [ ] **Step 5: 통과 확인** — `uv run pytest -q`
- [ ] **Step 6: 커밋** — `feat: langchain deps, .env loader and tracing switch (server off by default)`

---

### Task 2: LangChain 모델 호출 층 (Claude)

**Files:** Modify `pagegen/llm.py`, `tests/test_llm.py`

**Interfaces — Produces:** `llm.chat_model(*, thinking, max_tokens, temperature, model=None) -> ChatOpenAI`, `llm.chat`(같은 시그니처 + `model=None`), `llm.chat_json`(같은 시그니처 + `model=None`). `_post`는 사진 편집 전용으로 남는다.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_llm.py`에서 `_post`/`urlopen`으로 채팅을 가로채던 테스트 5개(`test_http_error_becomes_llm_error_with_body`, `test_connection_refused_becomes_llm_error`, `test_chat_never_shows_reasoning`, `test_chat_sends_schema_tools_and_thinking`, `test_server_dying_mid_response_becomes_llm_error`)를 아래로 바꾼다. 사진 편집·전처리·`parse_json`·`chat_json` 테스트는 그대로 둔다.

```python
import httpx
import openai
from langchain_core.messages import AIMessage


class FakeModel:
    """Stands in for ChatOpenAI: records how it was configured and returns one reply."""

    def __init__(self, reply, error=None):
        self.reply, self.error, self.bound, self.structured = reply, error, None, None

    def bind_tools(self, tools, **kw):
        self.bound = (tools, kw)
        return self

    def with_structured_output(self, schema, **kw):
        self.structured = (schema, kw)
        return self

    def invoke(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        return {"raw": self.reply, "parsed": None, "parsing_error": None} if self.structured else self.reply


def use(monkeypatch, model):
    made = {}
    monkeypatch.setattr(llm, "chat_model", lambda **kw: made.update(kw) or model)
    return made


def test_chat_converts_tool_calls(monkeypatch):
    reply = AIMessage(content="", tool_calls=[{"name": "update_text", "args": {"section_id": "s1"}, "id": "c1"}])
    use(monkeypatch, FakeModel(reply))
    assert llm.chat([], tools=[{"type": "function"}])["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "update_text", "arguments": '{"section_id": "s1"}'}}]


def test_chat_keeps_invalid_tool_calls_raw(monkeypatch):
    reply = AIMessage(content="", invalid_tool_calls=[{"name": "update_text", "args": "{section_id: s1", "id": "c2", "error": "bad"}])
    use(monkeypatch, FakeModel(reply))
    assert llm.chat([], tools=[{"type": "function"}])["tool_calls"] == [
        {"id": "c2", "type": "function", "function": {"name": "update_text", "arguments": "{section_id: s1"}}]


@pytest.mark.parametrize("content, shown", [
    ("<think>음…</think>\n좋아요", "좋아요"),
    ("음… 추론…</think>\n\n좋아요", "좋아요"),
    ("<think>사용자가 원하는 건", ""),
])
def test_chat_never_shows_reasoning(monkeypatch, content, shown):
    use(monkeypatch, FakeModel(AIMessage(content=content)))
    assert llm.chat([])["content"] == shown


def test_chat_configures_the_model(monkeypatch):
    model = FakeModel(AIMessage(content="{}"))
    made = use(monkeypatch, model)
    llm.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}], schema={"type": "object"},
             thinking=True, max_tokens=99, temperature=0.3, model="local/judge")
    assert made == {"thinking": True, "max_tokens": 99, "temperature": 0.3, "model": "local/judge"}
    assert model.bound[1] == {"tool_choice": "auto"}
    schema, kw = model.structured
    assert schema["type"] == "object" and schema["title"] == "out"
    assert kw == {"method": "json_schema", "strict": True, "include_raw": True}
    assert model.messages == [{"role": "user", "content": "x"}]


def test_chat_model_talks_to_mlx_serve():
    m = llm.chat_model(thinking=False, max_tokens=10, temperature=0.0)
    assert m.openai_api_base == f"{llm.MLX_URL}/v1" and m.model_name == llm.TEXT_MODEL and m.max_retries == 0
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


REQUEST = httpx.Request("POST", "http://127.0.0.1:11234/v1/chat/completions")


@pytest.mark.parametrize("error, message", [
    (openai.APIStatusError("bad", response=httpx.Response(400, text="PrefillDoesNotFit", request=REQUEST), body=None),
     "모델 서버 오류: 400 PrefillDoesNotFit"),
    (openai.APIConnectionError(request=REQUEST), "모델 서버 오류"),
    (openai.APITimeoutError(request=REQUEST), "모델 서버 오류"),
])
def test_api_errors_become_llm_error(monkeypatch, error, message):
    use(monkeypatch, FakeModel(None, error=error))
    with pytest.raises(llm.LLMError, match=message):
        llm.chat([])


@pytest.mark.parametrize("error", [http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"{")])
def test_image_server_dying_mid_response_becomes_llm_error(monkeypatch, error):
    def boom(*a, **k):
        raise error

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="모델 서버 오류"):
        llm._post("/v1/images/generations", {})


def test_image_http_error_keeps_the_body(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"PrefillDoesNotFit"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError, match="400 PrefillDoesNotFit"):
        llm._post("/v1/images/generations", {})
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_llm.py -q` → `chat_model` 없음 등으로 실패
- [ ] **Step 3: `pagegen/llm.py` 구현** — `chat`을 아래로 바꾸고 `chat_model`·`_openai_calls`를 더한다. `chat_json`은 `model` 인자를 받아 `chat(..., model=model)`로 넘긴다. 모듈 docstring을 "LangChain ChatOpenAI for chat; stdlib HTTP only for the non-standard image edit"으로.

```python
import openai
from langchain_openai import ChatOpenAI


def chat_model(*, thinking: bool = False, max_tokens: int = 1024, temperature: float = 0.0,
               model: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(base_url=f"{MLX_URL}/v1", api_key="local", model=model or TEXT_MODEL, temperature=temperature,
                      max_tokens=max_tokens, timeout=300, max_retries=0,
                      extra_body={"chat_template_kwargs": {"enable_thinking": thinking}})


def _openai_calls(message) -> list[dict]:
    """AIMessage tool calls back in OpenAI form. Calls whose arguments were not valid JSON keep their raw text,
    so the agent can hand the model its "인자 JSON" error instead of losing the call."""
    calls = [{"id": c.get("id") or "", "type": "function",
              "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
             for c in message.tool_calls]
    calls += [{"id": c.get("id") or "", "type": "function",
               "function": {"name": c.get("name") or "", "arguments": c.get("args") or ""}}
              for c in message.invalid_tool_calls]
    return calls


def chat(messages: list[dict], *, tools: list[dict] | None = None, schema: dict | None = None,
         thinking: bool = False, max_tokens: int = 1024, temperature: float = 0.0, model: str | None = None) -> dict:
    runnable = chat_model(thinking=thinking, max_tokens=max_tokens, temperature=temperature, model=model)
    if tools:
        runnable = runnable.bind_tools(tools, tool_choice="auto")
    if schema:
        runnable = runnable.with_structured_output({"title": "out", **schema}, method="json_schema", strict=True,
                                                   include_raw=True)
    try:
        reply = runnable.invoke(messages)
    except openai.APIStatusError as e:
        raise LLMError(f"모델 서버 오류: {e.status_code} {e.response.text[:200]}") from e
    except openai.OpenAIError as e:
        raise LLMError(f"모델 서버 오류: {e}") from e
    if isinstance(reply, dict):  # structured output: we parse the raw text ourselves (fences, retries)
        reply = reply["raw"]
    content = reply.content if isinstance(reply.content, str) else ""
    if "</think>" in content:
        content = content.rpartition("</think>")[2]
    elif content.lstrip().startswith("<think>"):
        content = ""  # thinking cut off by max_tokens: there is no answer, and the reasoning is never shown
    return {"content": content.strip(), "tool_calls": _openai_calls(reply)}
```

- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 통과(에이전트·서버 테스트 무수정)
- [ ] **Step 5: 실서버 확인** — `uv run python -c "from pagegen import llm; print(llm.chat_json([{'role':'user','content':'{\"a\": 1}을 그대로 JSON으로'}], {'type':'object','properties':{'a':{'type':'integer'}},'required':['a'],'additionalProperties':False}))"` → `{'a': 1}`; `uv run python scripts/eval_tools.py` → 도구 ≥ 11/13, 판정 8/8
- [ ] **Step 6: 커밋** — `feat: model calls through LangChain ChatOpenAI`

---

### Task 3: LangGraph 채팅 턴 (Claude)

**Files:** Modify `pagegen/agent.py`; 새 테스트는 `tests/test_graphs.py`

**Interfaces — Produces:** `agent.build_chat_graph(folder, page, corpus, basis, llm)` → 컴파일된 그래프(노드 `model`, `tools`). `chat_turn`은 같은 시그니처·반환.

- [ ] **Step 1: 실패하는 테스트** (`tests/test_graphs.py`)

```python
from pagegen import agent


def nodes(graph):
    return [n for n in graph.get_graph().nodes if not n.startswith("__")]


def test_chat_graph_has_model_and_tools(page, tmp_path):
    assert nodes(agent.build_chat_graph(tmp_path, page, "", "", llm=None)) == ["model", "tools"]
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_graphs.py -q` → AttributeError
- [ ] **Step 3: 구현** — `agent.py`에 추가하고 `chat_turn`의 루프를 그래프 호출로 바꾼다.

```python
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


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
```

`chat_turn`의 `log … else: reply = …` 부분을 다음으로 바꾼다:

```python
    graph = build_chat_graph(folder, page, corpus, basis, llm)
    state = graph.invoke({"messages": messages, "log": [], "changed": False, "reply": None, "rounds": 0},
                         {"recursion_limit": 4 * MAX_ROUNDS + 4})
    answer = {"role": "assistant", "text": state["reply"], "tools": state["log"]}
    chat.append(answer)
    return answer, state["changed"]
```

- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 통과(`tests/test_agent.py` 무수정)
- [ ] **Step 5: 커밋** — `feat: chat turn as a LangGraph graph`

---

### Task 4: LangGraph 첫 초안 (Claude)

**Files:** Modify `pagegen/agent.py`, `tests/test_graphs.py`

**Interfaces — Produces:** `agent.DRAFT_STEPS`, `agent.build_draft_graph(folder, llm)`. `make_draft`는 같은 시그니처·반환·예외.

- [ ] **Step 1: 실패하는 테스트** (`tests/test_graphs.py` 끝)

```python
def test_draft_graph_runs_the_fixed_order(tmp_path):
    assert agent.DRAFT_STEPS == ("analyze", "caption", "write_copy", "assemble", "check", "edit_scene", "first_message")
    assert nodes(agent.build_draft_graph(tmp_path, llm=None)) == list(agent.DRAFT_STEPS)
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — `make_draft`의 본문을 노드로 나눈다(각 노드는 지금 코드 그대로 옮긴 것):

```python
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
    """The fixed-order first draft (spec §4.1) as a LangGraph pipeline; every node is a step make_draft used to run inline."""
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
        page["analysis"]["photo_captions"] = ["", *(_caption(folder, p, llm) for p in page["photos"][1:3])]
        return {"page": page}

    def write_copy(s: DraftState) -> dict:
        page, captions = s["page"], s["page"]["analysis"]["photo_captions"]
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
    steps = dict(zip(DRAFT_STEPS, (analyze, caption, write_copy, assemble, check, edit_scene, first_message)))
    for name, fn in steps.items():
        graph.add_node(name, fn)
    for a, b in zip((START, *DRAFT_STEPS), (*DRAFT_STEPS, END)):
        graph.add_edge(a, b)
    return graph.compile()


def make_draft(folder: Path, pid: str, product: dict, photos: list[dict], llm=default_llm) -> tuple[dict, dict]:
    """Spec §4.1. Returns (page, first assistant message). Raises LLMError and then no draft exists."""
    page = {"id": pid, "version": 1, "product": product, "analysis": {}, "photos": photos, "sections": []}
    seller = f"상품명: {product['name']}\n판매자 설명: {product['notes'] or '(없음)'}"
    state = build_draft_graph(folder, llm).invoke({"page": page, "corpus": pg.corpus_of(page, []), "seller": seller})
    return state["page"], state["message"]
```

- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 통과(`tests/test_agent.py` 무수정)
- [ ] **Step 5: 커밋** — `feat: first draft as a LangGraph pipeline`

---

### Task 5: 실모델 검증과 문서 (Claude)

- [ ] **Step 1:** 서버를 새 코드로 다시 띄우고, Task 0과 같은 사진 2장 초안을 같은 명령으로 잰다. Task 0보다 10% 넘게 느리면 원인을 찾는다(systematic-debugging).
- [ ] **Step 2:** `uv run python scripts/eval_tools.py` → 도구 ≥ 11/13, 판정 8/8. `scripts/eval_tools.py` 맨 위 import 다음에 `pagegen.enable_dev_tracing("pagegen-dev")`를 더해, 키가 있으면 이 평가가 LangSmith에 남게 한다.
- [ ] **Step 3: 외부 전송 0 확인** — 서버를 `LANGSMITH_TRACING=true`로 띄워도 초안 1건 동안 `api.smith.langchain.com`으로 연결이 없는지 `lsof -i` 또는 `nettop`으로 확인한다(서버가 끈다).
- [ ] **Step 4:** README에 "LangChain·LangGraph 구조"(그래프 두 개 그림), "LangSmith(개발·학습만, `.env`의 `LANGSMITH_API_KEY`, 앱은 `PAGEGEN_TRACE=1`일 때만)" 절을 더한다.
- [ ] **Step 5: 커밋** — `docs: LangChain/LangGraph/LangSmith in README; eval_tools traces to LangSmith when keyed`

## 완료 조건

- `uv run pytest -q` 전체 통과, 에이전트·서버 테스트 무수정
- 실모델 도구 ≥ 11/13, 판정 8/8, 초안 시간 +10% 이내
- 앱 기본 실행 시 LangSmith 전송 없음
