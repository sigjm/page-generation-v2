# pagegen — 로컬 대화형 상세페이지 에이전트

상품 사진과 설명을 올리면 상세페이지 초안을 만들고, 채팅으로 고쳐 폭 860px 이미지로 내보냅니다.
모든 모델은 이 Mac의 MLX Serve에서 실행합니다(Qwen3.8-9B 4bit, Qwen-Image 2.1 4bit).

## 준비

1. MLX Serve 26.10.1 이상이 `http://127.0.0.1:11234`에서 두 모델을 서빙해야 합니다.
2. `uv sync && uv run playwright install chromium`

## 실행

```bash
uv run python -m pagegen.server
```

서버는 `http://127.0.0.1:8000`에서 열립니다.

## 설정 (환경 변수)

`MLX_URL`, `TEXT_MODEL`, `COPY_MODEL`(첫 초안 문구 모델, 기본 `TEXT_MODEL`. 2단계 채택 모델: `local/Qwen3.8-9B-pagegen-copy-r1-8bit`. 사진 설명이 없는 초안에만 쓰고, 사진이 여러 장이면 `TEXT_MODEL`), `IMAGE_MODEL`, `IMAGE_STEPS`, `PROJECTS_DIR`, `PAGEGEN_TRACE`(1이고 `.env`에 키가 있으면 앱도 LangSmith 프로젝트 `pagegen-app`에 추적), `LANGSMITH_API_KEY`·`LANGSMITH_PROJECT`(`.env`) — 기본값은 설계 문서 §2를 참고하세요.

## 구조 (LangChain · LangGraph)

- 모델 호출은 LangChain `ChatOpenAI`가 MLX Serve(OpenAI 호환 API)로 보냅니다. Qwen thinking은 `extra_body`, 도구는 `bind_tools`, JSON은 `json_schema` 구조화 출력입니다. 사진 편집만 OpenAI 표준이 아니라 직접 HTTP로 부릅니다.
- 에이전트 흐름은 LangGraph 그래프 두 개입니다.

```text
채팅 한 턴:  START → model ⇄ tools → END   (텍스트 답, ask_user, 4라운드에서 끝)
첫 초안:    START → analyze → caption → write_copy → assemble → check → edit_scene → first_message → END
```

- 상태는 `projects/<id>/`의 `page.json`·`chat.json`에 저장되므로 LangGraph 체크포인터는 쓰지 않습니다.

## LangSmith (개발·학습 데이터만)

- `.env`(git 제외)에 `LANGSMITH_API_KEY=…`를 넣으면, 평가·파인튜닝 스크립트(`scripts/eval_tools.py`, `scripts/finetune/*`)가 LangSmith에 추적·실험을 남깁니다.
- **앱 서버는 기본으로 추적을 끕니다**(셸에 `LANGSMITH_TRACING` 같은 값이 켜져 있어도 끕니다). 실제 상품 설명·문구를 LangSmith로 보내려면 `.env`에 키를 둔 채 `PAGEGEN_TRACE=1 uv run python -m pagegen.server`로 띄웁니다(프로젝트 `pagegen-app`).

## 지키는 규칙

- 업로드 원본은 바꾸지 않습니다. 사진 편집은 새 파일로 저장합니다.
- 입력·대화에 없는 숫자는 페이지에 들어가지 않습니다(기계 검사).
- 숫자가 아닌 제품 사실(기능·소재·관리법 등)은 원본 9B 판정기가 문장마다 검사하지만 일부를 놓칩니다. 실측에서 "습기나 물기에는 주의하세요"처럼 설명에 없는 관리 문구가 남은 적이 있으니, 올리기 전에 문구를 직접 확인하세요.
- 판정 모델 파인튜닝(r1~r3)은 시험셋에서 원본보다 나빠 채택하지 않았습니다. 기록은 `docs/finetune/`에 있습니다.

## 측정 (2026-10-16, 이 Mac)

스모크 실행에서 정사각·가로·세로 초안은 각각 **60.4초 / 86.5초 / 80.0초**였습니다. 문구 수정은 **3.4초**, 사용자 제공 무게를 표에 반영한 수정은 **15.9초**, 사진 편집 포함 수정은 **55.6초**였습니다. 전체 PNG 내보내기는 **1.8초**였습니다. 다시 재려면 서버를 띄운 뒤 `uv run python scripts/smoke.py --photo <사진> --out <폴더>`를 실행합니다.

## 테스트

```bash
uv run pytest
uv run python scripts/eval_tools.py   # 실모델 회귀
```
