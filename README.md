# pagegen — 로컬 대화형 상세페이지 에이전트

상품 사진과 설명을 올리면 상세페이지 초안을 만들고, 채팅으로 고쳐 폭 860px 이미지로 내보냅니다.
모든 모델은 이 Mac의 MLX Serve에서 실행합니다.

## 모델

| 역할 | 모델 | 환경 변수 |
|---|---|---|
| 사진 분석, 채팅, 도구 선택 | Qwen3.8-9B 4bit (`empero-ai/Qwen3.8-9B-Distill-MLX-4bit`) | `TEXT_MODEL` |
| 첫 초안 문구 | 2단계 SFT 모델 (`local/Qwen3.8-9B-pagegen-copy-r1-8bit`) | `COPY_MODEL` |
| 사실 판정(문장마다) | 원본 9B | `JUDGE_MODEL` |
| 사진 편집 | Qwen-Image 2.1 4bit (`ddalcu/Qwen-Image-2.1-MLX-Serve-4bit`) | `IMAGE_MODEL` |

- `COPY_MODEL`과 `JUDGE_MODEL`을 비워 두면 `TEXT_MODEL`을 씁니다.
- 문구 모델은 **사진 설명이 없는 초안(사진 한 장)에만** 씁니다. 사진이 여러 장이라 사진 설명이 붙으면 `TEXT_MODEL`이 씁니다. 이유는 [2-2 보고서](docs/finetune/2026-10-18-copy-2-2-report.md)에 있습니다.

## 준비

1. MLX Serve 26.10.1 이상을 `http://127.0.0.1:11234`에서 띄우고, 위 모델을 서빙합니다.
   - 새 모델 폴더를 `~/.mlx-serve/models/`에 넣었다면 MLX-Serve 앱을 다시 켜야 목록에 나옵니다.
   - MLX Serve는 목록에 없는 모델 이름을 받아도 오류를 내지 않고, 이미 올라와 있는 모델로 답합니다. 이름을 정확히 적으세요.
2. `uv sync && uv run playwright install chromium`

## 실행

```bash
COPY_MODEL=local/Qwen3.8-9B-pagegen-copy-r1-8bit uv run python -m pagegen.server
```

서버는 `http://127.0.0.1:8000`에서 열립니다. `COPY_MODEL` 없이 띄우면 첫 초안 문구도 원본 9B가 씁니다.

## 설정 (환경 변수)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `MLX_URL` | `http://127.0.0.1:11234` | MLX Serve 주소 |
| `TEXT_MODEL` | `empero-ai/Qwen3.8-9B-Distill-MLX-4bit` | 분석·채팅·도구 모델 |
| `COPY_MODEL` | `TEXT_MODEL` | 첫 초안 문구 모델(사진 설명 없는 초안만) |
| `JUDGE_MODEL` | `TEXT_MODEL` | 사실 판정 모델 |
| `IMAGE_MODEL` | `ddalcu/Qwen-Image-2.1-MLX-Serve-4bit` | 사진 편집 모델 |
| `IMAGE_STEPS` | `20` | 사진 편집 스텝 수 |
| `PROJECTS_DIR` | `projects` | 프로젝트 저장 폴더 |
| `PAGEGEN_TRACE` | 꺼짐 | `1`이고 `.env`에 키가 있으면 앱도 LangSmith 프로젝트 `pagegen-app`에 추적 |
| `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | `.env` | LangSmith 키와 프로젝트 |

## 구조 (LangChain · LangGraph)

- **모델 호출:** LangChain `ChatOpenAI`가 MLX Serve(OpenAI 호환 API)로 보냅니다.
  - Qwen thinking은 `extra_body`로 넘깁니다.
  - 도구는 `bind_tools`, JSON은 `json_schema` 구조화 출력입니다.
  - 사진 편집만 OpenAI 표준이 아니라서 직접 HTTP로 부릅니다.
- **에이전트 흐름:** LangGraph 그래프 두 개입니다.

```text
채팅 한 턴:  START → model ⇄ tools → END   (텍스트 답, ask_user, 4라운드에서 끝)
첫 초안:    START → analyze → caption → write_copy → assemble → check → edit_scene → first_message → END
```

- **상태 저장:** 상태는 `projects/<id>/`의 `page.json`·`chat.json`에 저장합니다. 그래서 LangGraph 체크포인터는 쓰지 않습니다.

## 지키는 규칙

- 업로드 원본은 바꾸지 않습니다. 사진 편집은 새 파일로 저장합니다.
- 입력·대화에 없는 숫자는 페이지에 들어가지 않습니다(기계 검사).
- 최상급 표현과 한자도 기계 검사로 뺍니다.
- 숫자가 아닌 제품 사실(기능·소재·관리법 등)은 원본 9B 판정기가 문장마다 검사합니다.
  - 다만 일부를 놓칩니다. 실측에서 "습기나 물기에는 주의하세요"처럼 설명에 없는 관리 문구가 남은 적이 있습니다.
  - 올리기 전에 문구를 직접 확인하세요.

## 파인튜닝 기록

| 단계 | 내용 | 결과 | 보고서 |
|---|---|---|---|
| 1단계 판정 LoRA (r1~r3) | 사실 판정기 학습 | **불채택.** 시험셋에서 원본보다 나쁨(F1 0.814 vs 최고 0.710) | [r1](docs/finetune/2026-10-16-judge-r1-report.md), [r3](docs/finetune/2026-10-17-judge-r3-report.md) |
| 2-1 문구 프롬프트 개선 | 초안 문구 프롬프트 수정 | **불채택.** 시험 블라인드 승률 0.414 | [2-1](docs/finetune/2026-10-17-copy-2-1-report.md) |
| 2-2 문구 SFT | Codex 교사 초안 349행으로 9B LoRA 학습 | **채택(사진 설명 없는 초안만).** 시험 블라인드 승률 0.897, 지어낸 문장 0.146→0.049, 시간 ×0.97 | [2-2](docs/finetune/2026-10-18-copy-2-2-report.md) |

- 2-2 모델은 한 페이지 안에서 같은 구절을 되풀이하는 약점이 있습니다. 시험에서 진 3건이 모두 이 경우였습니다.
- 사진 여러 장 초안에도 2-2를 쓰려면, 사진 설명이 있는 학습 행을 넣어 다시 학습해야 합니다.

### 스크립트 (`scripts/finetune/`)

| 스크립트 | 하는 일 |
|---|---|
| `copy_eval.py` | 문구 평가: `drafts`(초안 + 런타임 검사), `pairs`(블라인드 짝), `score`(채점), `sft`(학습 행) |
| `codex_teacher.py` | Codex 교사 데이터: `products`(가상 상품 300개), `inject`·`label`(판정 데이터), `copy`(교사 초안, train·dev만) |
| `judge_data.py`, `eval_judge.py` | 판정기 데이터와 평가 |
| `fuse_lora.py` | mlx-vlm LoRA를 4bit 원본에 합쳐 MLX Serve용 폴더로 저장(8bit) |

데이터와 어댑터는 `.local/finetune/`(git 제외)에 있습니다. 학습에는 합성 상품만 쓰고, `projects/`의 사용자 데이터는 쓰지 않습니다.

## LangSmith (개발·학습 데이터만)

- `.env`(git 제외)에 `LANGSMITH_API_KEY=…`를 넣으면, 평가·파인튜닝 스크립트(`scripts/eval_tools.py`, `scripts/finetune/*`)가 LangSmith에 추적과 실험을 남깁니다.
- 월간 추적 한도를 넘으면 429가 나옵니다. 이때는 `LANGSMITH_TRACING=false`를 주고 실행하세요. 결과에는 영향이 없습니다.
- **앱 서버는 기본으로 추적을 끕니다.** 셸에 `LANGSMITH_TRACING` 같은 값이 켜져 있어도 끕니다.
  - 실제 상품 설명·문구를 LangSmith로 보내려면 `.env`에 키를 둔 채 `PAGEGEN_TRACE=1 uv run python -m pagegen.server`로 띄웁니다(프로젝트 `pagegen-app`).

## 측정 (2026-10-16, 이 Mac)

| 작업 | 시간 |
|---|---|
| 첫 초안(정사각 / 가로 / 세로 사진) | 60.4초 / 86.5초 / 80.0초 |
| 문구 수정 | 3.4초 |
| 사용자가 준 무게를 표에 반영 | 15.9초 |
| 사진 편집 포함 수정 | 55.6초 |
| 전체 PNG 내보내기 | 1.8초 |

- 다시 재려면 서버를 띄운 뒤 `uv run python scripts/smoke.py --photo <사진> --out <폴더>`를 실행합니다.
- 이 측정은 2-2 채택 전입니다. 2-2 평가에서 문구 한 번의 초안 시간은 기준선과 같았습니다(×0.97).

## 테스트

```bash
uv run pytest                          # 단위 테스트
uv run python scripts/eval_tools.py    # 실모델 회귀(도구 ≥ 11/13, 판정 8/8)
```

## 문서

- 설계(스펙): `docs/superpowers/specs/`
- 구현 계획: `docs/superpowers/plans/`
- 파인튜닝 보고서와 검토: `docs/finetune/`
