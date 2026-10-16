# 대화형 상세페이지 에이전트 (page-generation-v2) 설계

- 작성: 2026-10-16
- 상태: 사용자 검토 대기
- 이전 버전: `../page-generation-improve` (v1, BE 연동 서비스). v2는 이식이 아니라 새 제품이다.

## 1. 목표

로컬 Mac에서만 도는 상세페이지 생성 에이전트. 사용자가 상품 사진과 몇 줄 설명을 올리면 초안을 만들고, 채팅으로 "헤드라인 짧게", "배경을 대리석으로"처럼 고친다. 결과는 쇼핑몰에 바로 올리는 폭 860px 세로 이미지다.

| 항목 | 결정 |
|---|---|
| 사용 방식 | 대화형. 첫 초안 후 채팅으로 수정 |
| 화면 | 로컬 웹 UI. 왼쪽 채팅, 오른쪽 실시간 미리보기 |
| 상품 | 일반 상품(의류·식품·가전 등) |
| 텍스트·비전 모델 | Qwen3.8-9B 4bit (MLX Serve) |
| 이미지 모델 | Qwen-Image 2.1 4bit. **업로드 사진 편집(연출 컷)에만** 쓴다 |
| 문구의 사실 | 사용자 입력·대화·사진에 근거한 것만. 모르면 되묻는다 |
| 출력 | 폭 860px 긴 PNG·JPG 한 장 + 섹션별 PNG |
| 외부 호출 | 없음. 모델은 로컬 MLX Serve만, 폰트는 시스템 폰트 |

### 성공 기준

- 사진 1~6장 + 설명으로 2분 안에 첫 초안이 미리보기에 뜬다.
- 채팅 수정 한 번이 문구만이면 30초, 사진 편집 포함이면 90초 안에 반영된다.
- 사용자가 쓰지 않은 숫자(용량·무게·가격 등)는 페이지에 들어가지 않는다.
- 업로드 원본 파일은 바뀌지 않는다.

### 범위 밖

로그인, 다중 사용자, DB, React·빌드 도구, 스트리밍 응답, 섹션 이미지를 텍스트째 생성하는 기능, 클라우드 배포. 필요해지면 그때 추가한다.

## 2. 구성

```text
브라우저 (static/index.html, vanilla JS)
  ├─ 왼쪽: 업로드 폼(사진·상품명·설명) → 채팅
  └─ 오른쪽: <iframe src=/projects/{id}/page>  변경마다 새로고침
        │
FastAPI (pagegen/server.py, 127.0.0.1:8000)
        │
pagegen/agent.py   초안 파이프라인 + 채팅 도구 루프
pagegen/page.py    페이지 상태, 도구 7개, 근거 검사, 버전 저장
pagegen/render.py  페이지 JSON → HTML(stdlib html.escape), Playwright 캡처
pagegen/llm.py     MLX Serve 클라이언트(채팅·도구·비전, 이미지 편집), stdlib urllib
        │
MLX Serve :11234  (앱으로 이미 실행 중, --model-dir ~/.mlx-serve/models)
```

- 의존성: `fastapi`, `uvicorn`, `python-multipart`, `pillow`, `playwright`. Python 3.13, uv.
- 패키지는 저장소 루트의 `pagegen/` 하나. `src/` 레이아웃을 쓰지 않는다.
- 모델 작업은 한 번에 하나(전역 `threading.Lock`). 단일 사용자 전제다.

### 설정 (환경 변수, 기본값)

| 이름 | 기본값 |
|---|---|
| `MLX_URL` | `http://127.0.0.1:11234` |
| `TEXT_MODEL` | `empero-ai/Qwen3.8-9B-Distill-MLX-4bit` |
| `IMAGE_MODEL` | `ddalcu/Qwen-Image-2.1-MLX-Serve-4bit` |
| `IMAGE_STEPS` | `20` (4단계는 흐릿한 결과만 나온다, v1 실측) |
| `PROJECTS_DIR` | `projects` |

`TEXT_MODEL`을 `local/Qwen3.8-9B-kcdf-agent-4bit`(v1 run-1)로 바꾸면 파인튜닝 모델을 쓴다. 기본값은 §9의 실측으로 정한다.

## 3. 페이지 상태 (`page.json`) — Claude·Codex 공통 계약

```json
{
  "id": "20261016-153012-ab12",
  "version": 3,
  "product": {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종"},
  "analysis": {
    "category": "주방용품",
    "visible_features": ["무광 스테인리스 표면", "원터치 뚜껑"],
    "colors": ["실버"],
    "questions": ["무게는 얼마인가요?"]
  },
  "photos": [
    {"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
    {"id": "p1-e1", "file": "photos/p1-e1.png", "origin": "edit", "source": "p1", "instruction": "밝은 대리석 테이블 위"}
  ],
  "sections": [
    {"id": "s1", "kind": "hero",       "headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러", "photo": "p1"},
    {"id": "s2", "kind": "points",     "headline": "이래서 좋아요", "body": "넉넉한 용량: 500ml로 하루 커피 두 잔\n원터치 뚜껑: 한 손으로 열고 닫기"},
    {"id": "s3", "kind": "scene",      "headline": "책상 위 어디서나", "body": "", "photo": "p1-e1"},
    {"id": "s4", "kind": "spec_table", "headline": "제품 정보", "body": "용량: 500ml\n색상: 3종"},
    {"id": "s5", "kind": "notice",     "headline": "관리 방법", "body": "중성세제로 세척해 주세요."}
  ]
}
```

- 섹션 필드는 모든 종류가 같다: `id`, `kind`, `headline`, `body`, `photo`(있을 때만).
- `kind`는 5종: `hero`, `points`, `scene`, `spec_table`, `notice`.
- `points`·`spec_table`의 `body`는 한 줄에 하나씩 `"제목: 설명"` / `"항목: 값"`. 렌더러가 첫 `": "`에서 나눈다. `notice`의 `body`는 줄바꿈을 문단 구분으로 쓴다.
- `photo`가 필요한 종류는 `hero`, `scene`. 나머지는 `photo`를 무시한다.
- `photos[].file`은 프로젝트 폴더 기준 상대 경로. 업로드는 `origin: "upload"`, 편집본은 `origin: "edit"`와 `source`(원본 id)를 가진다.
- 섹션 id는 `s1`부터, 새 섹션은 기존 최대 번호 + 1. 사진 id는 업로드 `p1…pN`, 편집본 `<원본id>-e<번호>`.

### 프로젝트 폴더

```text
projects/<id>/
├── page.json            현재 상태
├── versions/page.<N>.json  변경 전 상태(되돌리기용)
├── chat.json            [{"role":"user"|"assistant","text":"…","tools":[…]}]
├── photos/              업로드 원본(p1.jpg…)과 편집본(p1-e1.png…)
└── export/              page.png, page.jpg, sections/01-hero.png…
```

프로젝트 id는 `^\d{8}-\d{6}-[0-9a-f]{4}$`만 받는다(경로 조작 방지).

## 4. 처리 흐름

### 4.1 첫 초안 (고정 순서)

1. 업로드 저장. 이미지가 아니면(Pillow로 열리지 않으면) 400. 최대 6장, 장당 20MB. 파일명은 서버가 정한다(`p1.jpg`…). 모델에는 긴 변 1,280px로 줄인 사본을 보낸다(최대 4장).
2. **분석** (비전, `response_format: json_schema`): `category`, `visible_features[]`, `colors[]`, `facts[]`(설명에서 뽑은 `{key, value}`), `questions[]`(빠진 중요 정보, 최대 3개).
3. **문구** (텍스트, `json_schema`): 입력은 상품명·설명·분석 결과. 출력은 `hero{headline, body}`, `points[{title, text}]`(3개), `scene{headline, body, photo_instruction}`, `notice{headline, body}`.
4. **섹션 조립**: `hero(p1)` → `points` → `scene` → `spec_table`(분석 `facts` 중 근거 검사를 통과한 것, 없으면 섹션 생략) → `notice`(본문이 비면 생략).
5. **연출 컷 1장**: `scene.photo_instruction`으로 `p1`을 편집해 `scene` 섹션에 넣는다. 실패하면 `p1`을 그대로 쓴다.
6. 근거 검사(§5)를 통과하지 못한 줄은 버리고, 헤드라인이 걸리면 상품명으로 바꾼다.
7. 첫 assistant 메시지: 초안 완료 + `questions`가 있으면 그 질문들.

2와 3을 나눈 이유: 한 번에 긴 JSON을 내게 하면 9B가 같은 문장을 반복하다 출력 한도에서 잘리는 문제가 v1에서 있었다. 각 호출은 `max_tokens` 1,500으로 제한한다. JSON이 깨지면 한 번 다시 부르고(temperature 0.3), 또 깨지면 오류를 보여주고 초안을 만들지 않는다.

### 4.2 채팅 수정 (도구 호출)

```text
update_text(section_id, field, text)            field: headline | body
edit_photo(section_id, instruction)             그 섹션 사진의 원본을 편집해 새 사진으로 교체
use_photo(section_id, photo_id)                 기존 사진(원본·이전 편집본)으로 교체 = "원래 사진으로"
add_section(kind, after_section_id, headline, body)
remove_section(section_id)
move_section(section_id, after_section_id)      after_section_id가 "" 이면 맨 앞
ask_user(question)                              모르는 사실은 지어내지 말고 묻는다. 호출하면 턴 종료
```

- 모델 입력: 시스템 프롬프트(규칙 + 현재 `page.json`에서 `analysis` 뺀 압축 JSON) + 최근 대화 6턴(텍스트만) + 이번 메시지.
- 루프: 모델이 `tool_calls`를 내면 순서대로 실행하고 결과(성공 요약 또는 오류 문장)를 `tool` 메시지로 돌려준 뒤 다시 부른다. 도구 호출 없이 텍스트로 답하면 그게 이번 턴의 답이다. 최대 4라운드. 넘으면 "여기까지 반영했어요"와 실행한 도구 목록으로 끝낸다.
- 도구 오류(없는 id, 잘못된 kind, 근거 없는 숫자)는 예외가 아니라 모델에게 돌려주는 문장이다. 예: `"오류: 근거 없는 숫자 350. 사용자에게 ask_user로 물어보세요."`
- 페이지를 바꾸는 도구가 하나라도 성공하면 턴 시작 전 상태를 `versions/page.<N>.json`으로 저장하고 `version`을 올린다(턴당 한 번).
- `edit_photo`는 항상 **업로드 원본**(`origin: "upload"`)에서 시작한다. 편집본을 다시 편집하지 않아 화질이 누적해 떨어지지 않는다.
- `add_section`으로 만든 `hero`·`scene`의 `photo`는 `p1`으로 시작한다.

### 4.3 되돌리기

UI의 "되돌리기" 버튼 → `POST /projects/{id}/undo` → 가장 최근 `versions/page.<N>.json`을 `page.json`으로 복원하고 그 파일을 지운다. 도구가 아니라 버튼이다. 사진 파일은 지우지 않는다.

## 5. 근거 규칙

- **근거 텍스트** = 상품명 + 설명(`notes`) + 사용자가 채팅에 쓴 모든 메시지. 사용자가 "350g이에요"라고 답하면 그 뒤로 350은 근거가 있다.
- **숫자 검사**: 새 문구(`headline`·`body`)에서 정규식 `\d+(?:[.,]\d+)?`로 뽑은 숫자가, 근거 텍스트에서 같은 정규식으로 뽑은 숫자 집합에 없으면 거절한다(부분 문자열 비교가 아니다: "5"는 "500ml"로 근거가 생기지 않는다). 단위는 보지 않는다.
- **스펙 표 검사**: `spec_table`의 각 줄 값(`": "` 뒤)이 근거 텍스트에 공백을 무시하고 그대로 들어 있어야 한다.
- 숫자가 아닌 주장(예: "식기세척기 사용 가능")은 시스템 프롬프트 규칙으로만 막는다.
  `# ponytail: 숫자·스펙 표만 기계 검사. 비수치 주장까지 막으려면 별도 판정 호출을 추가`
- 사진에서 보이는 것(색·모양)은 분석 결과가 근거다. 근거 텍스트에 넣지는 않는다(숫자가 거의 없다).

## 6. 사진 규칙

- 업로드 원본 파일은 덮어쓰지 않는다. 편집은 항상 새 파일이다.
- `hero`는 원본으로 시작한다. 사용자가 요청하면 편집본도 넣을 수 있다(미리보기로 확인하고 `use_photo`로 되돌린다).
- 편집 요청: Qwen-Image 2.1 `mode: "edit"`, `IMAGE_STEPS` 단계. 정확한 엔드포인트·필드·출력 크기는 §9 실측으로 확정해 `llm.py` 한 곳에만 둔다. 출력 크기 기본값은 1024×1024.

## 7. API

| 메서드·경로 | 입력 | 출력 |
|---|---|---|
| `GET /` | — | `static/index.html` |
| `POST /projects` | multipart: `photos[]`, `name`, `notes` | `{id, version, messages}` (초안 완료 후 응답) |
| `GET /projects/{id}` | — | `{page, chat}` (새로고침 복원용) |
| `POST /projects/{id}/chat` | `{"text": "…"}` | `{reply, tools, version}` |
| `POST /projects/{id}/undo` | — | `{version}`, 되돌릴 게 없으면 409 |
| `GET /projects/{id}/page` | — | 페이지 HTML (미리보기·캡처 공용) |
| `GET /projects/{id}/photos/{file}` | — | 사진 파일 |
| `POST /projects/{id}/export` | — | `{png, jpg, sections[]}` (파일 URL) |

- 서버는 `127.0.0.1`에만 바인딩한다.
- UI는 프로젝트 id를 URL 해시(`/#<id>`)에 두어 새로고침해도 이어진다.
- 요청 처리 중에는 UI가 입력을 막고 스피너를 보인다.

## 8. 렌더링 (Codex 담당)

- `render.py`의 `render_html(page, photo_url) -> str` 하나가 미리보기와 캡처에 같이 쓰인다. 모델이 쓴 모든 문자열은 `html.escape`를 거친다.
- 폭 860px 고정, 섹션마다 `<section data-section-id="s1" data-kind="hero">`.
- 폰트는 시스템 폰트(`-apple-system, "Apple SD Gothic Neo", sans-serif`). 외부 CDN을 쓰지 않는다.
- 섹션 종류마다 디자인 1개. 변형·테마 선택은 범위 밖.
- 내보내기: Playwright(Chromium)로 `/projects/{id}/page`를 열어 전체 페이지 PNG → `page.png`, Pillow로 `page.jpg`(품질 90), 각 `section` 요소 캡처 → `sections/NN-<kind>.png`.

## 9. 실측으로 확정할 기본값

Codex 스파이크(`.orchestration/tasks/2026-10-16-mlx-probe.md`) 결과로 아래를 정하고 이 표를 고친다.

| 항목 | 현재 기본값 | 바꾸는 조건 |
|---|---|---|
| `TEXT_MODEL` | base 9B | run-1의 도구 정답률이 base보다 높고 7번(근거 없는 주장)에서 지어내지 않으면 run-1 |
| thinking 출력 | 끔(`enable_thinking: false`) | 끄면 도구 정답률이 크게 떨어질 때만 켬 |
| 수정 루프 방식 | 도구 호출 | 두 모델 모두 도구 정답률 7/10 미만이면 수정 루프만 `json_schema` 패치로 바꾼다 |
| 이미지 편집 형식·크기 | 1024×1024 | 실측한 엔드포인트·필드 그대로, 비정사각 입력 처리 방식에 맞춰 조정 |

## 10. 오류 처리

| 상황 | 동작 |
|---|---|
| MLX Serve 연결 실패·4xx·5xx | 페이지 변경 없음. 채팅에 "모델 서버 오류: <응답 본문 앞 200자>" |
| 초안 JSON 두 번 깨짐 | 초안을 만들지 않고 오류 표시. 업로드 사진은 남는다 |
| 사진 편집 실패 | 이전 사진 유지, 채팅에 실패 사실 표시 |
| 도구 인자 오류 | 모델에게 오류 문장으로 돌려줌(§4.2) |
| 도구 라운드 4회 초과 | 그때까지 반영분 유지, 실행한 도구 목록과 함께 종료 |

## 11. 테스트

- `tests/test_page.py`: 도구 7개(없는 id, 순서 변경, `use_photo`, 섹션 id 부여), 숫자·스펙 표 근거 검사(근거 없는 숫자 거절, 채팅 답변 후 허용), 버전 저장·되돌리기, HTML 이스케이프.
- `tests/test_agent.py`: 가짜 LLM 함수로 채팅 루프 — 도구 오류가 모델에게 돌아감, `ask_user`로 턴 종료, 4라운드 상한, 초안 JSON 재시도.
- `scripts/smoke.py`: 실제 MLX Serve로 사진 1장 → 초안 → 채팅 3건 → 내보내기, 단계별 시간 기록.
- `scripts/eval_tools.py`: 스파이크의 요청 10개를 보존해 모델을 바꿀 때 도구 정답률을 다시 잰다.

## 12. 작업 분담

| 누가 | 맡는 것 | 파일 |
|---|---|---|
| Claude (지휘) | 스펙·계획·핵심 로직·검수·커밋 | `pagegen/page.py`, `pagegen/agent.py`, `pagegen/llm.py`, `pagegen/server.py`, `tests/` |
| Codex (워커) | 렌더링·UI·실모델 검증 | `pagegen/render.py`, `static/index.html`, `scripts/smoke.py`, `scripts/eval_tools.py`, 측정 보고 |

- 둘이 공유하는 계약은 §3의 `page.json`과 §8의 `render_html(page, photo_url)` 시그니처뿐이다.
- Codex에게는 `.orchestration/tasks/<날짜>-<주제>.md` 브리프로 맡기고 터미널에는 한 줄만 보낸다. 결과는 Claude가 diff·테스트로 확인한 뒤 받는다. Codex는 git 상태를 바꾸지 않는다.
- `.orchestration/`, `projects/`, `.venv/`는 git에 넣지 않는다.
