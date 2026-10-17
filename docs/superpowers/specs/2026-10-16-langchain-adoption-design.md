# LangChain·LangGraph·LangSmith 적용 설계 (0단계)

- 작성: 2026-10-16
- 상태: 사용자 검토 대기
- 관련: 기본 스펙 `2026-10-16-conversational-detail-page-agent-design.md`, 판정 파인튜닝 스펙 `2026-10-16-judge-finetune-design.md`(이 문서가 §6 평가 방식을 바꾼다)

## 1. 목표

- 모델 호출을 LangChain(`ChatOpenAI`)으로, 에이전트 흐름(채팅 턴·첫 초안)을 LangGraph 상태 그래프로 옮긴다.
- 개발·학습 데이터의 추적·데이터셋·평가 실험을 LangSmith로 한다. 파인튜닝(1단계)의 원본 vs 학습 모델 비교를 LangSmith 실험으로 본다.
- **동작은 바꾸지 않는다.** 사용자가 보는 결과·API·page.json은 그대로다.

### 합격 기준

- `uv run pytest -q` 전체 통과(에이전트·서버 테스트는 내용 수정 없이 통과; 모델 호출 층 테스트만 새 구현에 맞게 바뀜)
- `scripts/eval_tools.py`: 도구 ≥ 11/13, 판정 8/8 (옮기기 전 12/13·8/8)
- 실서버 초안 1건(사진 2장) 정상, 초안 시간이 옮기기 전보다 10% 넘게 늘지 않음
- 앱 서버를 기본 설정으로 띄웠을 때 LangSmith로 아무것도 나가지 않음

### 범위 밖

LangGraph 체크포인트·`interrupt`(§3에 이유), LangGraph Platform/Studio 배포, LangSmith 프롬프트 허브, 앱 실행 트레이스 수집(사용자 결정: 개발·학습 데이터만).

## 2. LangChain: 모델 호출 층 (`pagegen/llm.py`)

- `chat_model(*, thinking, max_tokens, temperature, model) -> ChatOpenAI`: `ChatOpenAI(base_url=f"{MLX_URL}/v1", api_key="local", model=model or TEXT_MODEL, temperature=…, max_tokens=…, timeout=300, max_retries=0, extra_body={"chat_template_kwargs": {"enable_thinking": thinking}})`. 실측(2026-10-16): MLX Serve 26.10.2에서 thinking 끔·도구 호출·`json_schema` 구조화 출력 모두 동작.
- `chat(messages, *, tools, schema, thinking, max_tokens, temperature, model) -> dict`: 시그니처와 반환 모양(`{"content", "tool_calls": [OpenAI 형식]}`)을 그대로 둔다. 내부는 `chat_model(...)`에 `bind_tools(tools)`(OpenAI 형식 도구 정의를 그대로) 또는 `response_format`(json_schema)를 붙여 `invoke(messages)`.
  - 메시지는 지금처럼 OpenAI 형식 dict로 받는다(LangChain이 그대로 변환한다 — 실측 확인).
  - 반환: `AIMessage.tool_calls`를 OpenAI 형식(`arguments`는 JSON 문자열)으로 되돌린다. **`invalid_tool_calls`(인자 JSON이 깨진 호출)도 원문 `arguments` 그대로 포함**해, 에이전트가 지금처럼 "인자 JSON" 오류를 모델에 돌려주게 한다.
  - 본문의 `<think>` 처리(닫힘·여는 태그 없음·잘림)는 지금 규칙 그대로.
- `chat_json(messages, schema, *, max_tokens, model) -> dict`: `chat(..., schema=…)` + `parse_json` + temperature 0.3 재시도 1회 — 지금과 같다.
- 오류: `openai.APIStatusError` → `LLMError(f"모델 서버 오류: {status} {본문 앞 200자}")`, `openai.APIConnectionError`·`APITimeoutError`·그 밖의 `openai.OpenAIError` → `LLMError("모델 서버 오류: …")`. 기존 메시지 형식을 지킨다.
- 사진 편집(`edit_image`)은 OpenAI 표준이 아닌 필드(`mode: "edit"`, `steps`, `strength`)라 지금의 urllib `_post`를 그대로 쓴다.
- `JUDGE_MODEL`(1단계 Task 1)은 `chat`·`chat_json`의 `model` 인자로 들어간다.

## 3. LangGraph: 에이전트 흐름 (`pagegen/agent.py`)

### 채팅 턴 그래프

```text
START → model ─(tool_calls 없음)──────────────→ END
          │                                    ↑
          └─(tool_calls 있음)→ tools ─(ask_user·4라운드)─┘
                                 └─(계속)→ model
```

- 상태(`TypedDict`): `messages`(OpenAI 형식 dict), `log`, `changed`, `reply`, `rounds`.
- `model` 노드: `llm.chat(messages, tools=prompts.TOOLS, thinking=True, max_tokens=1024)`. 도구 호출이 없으면 `reply`를 채우고 끝.
- `tools` 노드: 지금의 `_args`·`_run_tool`·`ask_user` 처리를 그대로 부른다. `ask_user`면 `reply`를 질문으로 채우고 끝. `rounds`가 4면 "여기까지 반영했어요."로 끝.
- `chat_turn(folder, page, chat, text, llm)`의 시그니처·반환·부수 효과(page·chat 제자리 변경)는 그대로. 내부에서 그래프를 만들어(`build_chat_graph(folder, page, corpus, basis, llm)`) `invoke`한다. `recursion_limit`은 라운드 상한보다 넉넉히(20).
- 도구는 `ToolNode` 대신 직접 만든 노드다: 도구가 page·근거 텍스트·판정기를 클로저로 쓰고, 실패를 예외 대신 모델에 돌려주는 문장(`ToolError`)으로 다룬다.

### 첫 초안 그래프

```text
START → analyze → captions → copy → assemble → clean → scene → message → END
```

- 각 노드는 지금 `make_draft` 안의 단계를 그대로 옮긴 것이다(분석 호출, split 사진 설명, 문구 호출, `build_sections`+`_settle`, 문장 검사+`_settle`+id, 연출 컷 편집, 첫 메시지).
- `make_draft(folder, pid, product, photos, llm)`의 시그니처·반환·예외(`LLMError`)는 그대로.

### 체크포인트·interrupt를 쓰지 않는 이유

상태는 이미 `store.py`가 `page.json`·`chat.json`·`versions/`로 저장하고(되돌리기 포함), 사용자에게 묻는 일(`ask_user`)은 HTTP 응답으로 끝난다. LangGraph 체크포인터를 더하면 같은 상태를 두 곳에 저장하게 된다.
`# ponytail: 그래프는 요청마다 새로 만들고 체크포인터 없이 돈다. 긴 작업을 중간에 멈췄다 잇는 기능이 필요해지면 SqliteSaver를 붙인다`

## 4. LangSmith: 개발·학습 데이터만

- 키: 저장소 루트의 `.env`(git 제외, 이미 `.gitignore`에 추가할 것)에서 `KEY=VALUE` 줄을 읽어 환경 변수에 없을 때만 넣는다(`pagegen/__init__.py`, stdlib 몇 줄). 쓰는 키: `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`(기본 `pagegen-dev`).
- **앱 서버**: 시작할 때 `PAGEGEN_TRACE`가 `1`이 아니면 `LANGSMITH_TRACING=false`로 강제한다. 사용자의 실제 상품 설명·문구는 기본으로 나가지 않는다.
- **스크립트**(`scripts/eval_tools.py`, `scripts/finetune/*`): `LANGSMITH_API_KEY`가 있으면 `LANGSMITH_TRACING=true`·프로젝트 `pagegen-finetune`으로 추적한다. 키가 없으면 추적 없이 로컬로만 돈다.
- **판정 평가(파인튜닝 스펙 §6 대체)**: `scripts/finetune/eval_judge.py`가
  1. 로컬 지표(정밀도·재현율·F1·잘못 거르기·실패·지연)를 지금처럼 계산·출력하고,
  2. 키가 있으면 평가셋을 LangSmith 데이터셋 `pagegen-judge-eval`로 올리고(이미 있으면 그대로 사용) `langsmith.evaluate`로 실험(`judge-<모델 이름>`)을 만든다. 평가자는 문장 단위 정답 일치와 "근거 없음 판정" 두 개. 원본 9B·r1 실험을 LangSmith 비교 화면에서 나란히 본다.
- 보내는 데이터: 가상 상품·판정 문장·모델 출력(합성)뿐. `projects/`의 사용자 데이터는 스크립트가 읽지 않는다.

## 5. 의존성

`pyproject.toml`의 런타임 의존성에 추가: `langchain-core>=1.6`, `langchain-openai>=1.7`, `langgraph>=1.2`, `langsmith>=0.14`(2026-10-16 최신: 1.6.11 / 1.7.2 / 1.2.15 / 0.14.11). `openai`는 `langchain-openai`가 끌어온다.

## 6. 테스트

- 에이전트·서버 테스트(`tests/test_agent.py`, `tests/test_server.py`): **수정 없이** 통과해야 한다. `FakeLLM`이 `llm.chat`·`llm.chat_json` 자리를 대신하므로 그래프 안에서도 그대로 쓰인다.
- 모델 호출 층(`tests/test_llm.py`): urllib을 가로채던 채팅 테스트를 `chat_model`을 가짜 모델로 바꾸는 방식으로 다시 쓴다 — 도구 호출 변환, 깨진 인자 보존, `<think>` 처리, json_schema 전달, thinking 전달, 오류 → `LLMError` 변환, `JUDGE_MODEL`. 사진 편집 테스트는 그대로.
- 새 테스트: 그래프 구조(채팅 그래프 노드·끝 조건, 초안 그래프 노드 순서), 서버 시작 시 추적 강제 끔, `.env` 읽기(환경 변수가 우선).

## 7. 분담

0단계는 Claude가 한다(에이전트 핵심 구조라 파인튜닝 데이터 작업과 겹치지 않게). Codex는 같은 시간에 파인튜닝 1단계 Task 2(가상 상품 생성)를 한다 — 모델 호출 층과 무관하다.
