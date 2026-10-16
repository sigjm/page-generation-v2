# 대화형 상세페이지 에이전트 (page-generation-v2) 설계

- 작성: 2026-10-16
- 상태: 승인됨 (2026-10-16). 구현 계획: `docs/superpowers/plans/2026-10-16-conversational-detail-page-agent.md`
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
    "facts": [{"key": "용량", "value": "500ml"}, {"key": "색상", "value": "3종"}],
    "questions": ["무게는 얼마인가요?"],
    "photo_captions": ["정면 사진", "뚜껑을 연 모습"]
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

- `analysis`는 첫 초안 때 한 번 만들고 바꾸지 않는다. 모든 키가 필수이며 빈 목록을 허용한다. `facts`는 설명(`notes`)에서 뽑은 `{key, value}`이고, `value`가 §5 스펙 표 검사를 통과한 것만 저장한다. `visible_features`·`colors`·`photo_captions`는 주장 검사(§5)의 사진 근거로 쓴다. `photo_captions`는 모델에 보낸 사진(최대 4장) 순서대로 한 문장씩이며, 빈 값도 자리를 지킨다(섹션 레이아웃 스펙 §4).
- 섹션 필드는 모든 종류가 같다: `id`, `kind`, `headline`, `body`, `photo`(있을 때만).
- `kind`는 5종: `hero`, `points`, `scene`, `spec_table`, `notice`.
- `points`·`spec_table`의 `body`는 한 줄에 하나씩 `"제목: 설명"` / `"항목: 값"`. 근거 검사와 렌더러 모두 첫 `":"`에서 나누고 양쪽 공백을 지운다(`page.split_line`). 9B가 `"용량:500ml"`처럼 공백 없이 쓰거나 값에 `:`이 들어가도(`"10:30"`) 같은 결과가 나온다. `notice`의 `body`는 줄바꿈을 문단 구분으로 쓴다.
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

1. 업로드 저장. 최대 6장, 장당 20MB. Pillow로 형식을 판별해 JPEG·PNG·WebP만 받고(HEIC 등은 400 "JPG·PNG로 변환해 올려 주세요"), `Image.MAX_IMAGE_PIXELS = 40_000_000`으로 압축 폭탄을 막는다. **원본 바이트를 그대로** 저장하고, 파일명은 서버가 판별한 형식으로 정한다(`p1.jpg`·`p1.png`·`p1.webp`). 모델에는 EXIF 방향을 적용(`ImageOps.exif_transpose`)하고 긴 변 1,280px로 줄인 JPEG 사본을 보낸다(최대 4장).
2. **분석** (비전, `response_format: json_schema`): `category`, `visible_features[]`, `colors[]`, `facts[]`(설명에서 뽑은 `{key, value}`), `questions[]`(빠진 중요 정보, 최대 3개).
3. **문구** (텍스트, `json_schema`): 입력은 상품명·설명·분석 결과. 출력은 `hero{headline, body}`, `points{headline, items[{title, text}]}`(items 3개), `scene{headline, body, photo_instruction}`, `notice{headline, body}`.
4. **섹션 조립**: `hero(p1)` → `points` → `scene` → `spec_table`(분석 `facts` 중 근거 검사를 통과한 것, 없으면 섹션 생략) → `notice`(본문이 비면 생략).
5. **연출 컷 1장**: `scene.photo_instruction`으로 `p1`을 편집해 `scene` 섹션에 넣는다. 실패하면 `p1`을 그대로 쓴다.
6. **초안 검사**: 모델이 만든 모든 문구(`hero`·`points`·`scene`·`notice`의 `headline`·`body`)에 숫자 검사와 주장 검사(§5)를 건다. 주장 검사는 **문장마다** 판정 호출 1회다(헤드라인 1회 + 본문 문장마다 1회). 실제 초안에서 섹션을 통째로 판정하면 긴 문구 속 주장을 놓치거나, 걸린 구절을 찾지 못해 좋은 문장까지 버렸다. 걸린 문장만 버리고, 걸린 `headline`은 상품명으로 바꾼다. `points` 줄은 한 부분이라도 걸리면 줄 전체를 버린다(제목만 남은 반쪽 항목 방지). 본문이 빈 `points`·`notice`·`spec_table`은 뺀다. `spec_table`은 `facts`에서 만들므로 스펙 표 검사만 받는다. 판정이 두 번 실패한 문장은 그 문장만 버리고(헤드라인이면 상품명으로 바꾼다), 판정이 **전부** 실패했을 때만 초안을 만들지 않고 오류를 보여준다. 어느 쪽이든 판정받지 못한 문구는 페이지에 들어가지 않는다. 실사용에서 9B 판정기가 한 문장에 대해 출력 한도(당시 400토큰)까지 반복해 초안 전체가 실패한 일이 있었다(2026-10-16). 판정 `max_tokens`는 800이다.
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
- 시스템 프롬프트 규칙(실측 반영): ① "짧게·감성적으로"처럼 문구를 다듬는 요청은 되묻지 말고 바로 고친다. 섹션을 말하지 않으면 `hero`를 고친다. ② 감사·잡담에는 도구 없이 한 문장으로 답한다. ③ 사용자가 말하지 않은 제품 사실(기능·소재·성능·인증)은 새로 쓰지 않는다. 사용자가 직접 말한 사실은 쓴다. `ask_user`는 사실이 필요한데 없을 때만 쓴다. 스파이크에서는 "지어내지 말고 ask_user"만 강조해 문구 다듬기 요청까지 되묻는 일이 4설정 모두에서 났다.
- 루프: 모델이 `tool_calls`를 내면 순서대로 실행하고 결과(성공 요약 또는 오류 문장)를 `tool` 메시지로 돌려준 뒤 다시 부른다. 도구 호출 없이 텍스트로 답하면 그게 이번 턴의 답이다. 최대 4라운드. 넘으면 "여기까지 반영했어요"와 실행한 도구 목록으로 끝낸다.
- 도구 오류(없는 id, 잘못된 kind, 근거 없는 숫자)는 예외가 아니라 모델에게 돌려주는 문장이다. 예: `"오류: 근거 없는 숫자 350. 사용자에게 ask_user로 물어보세요."`
- 페이지를 바꾸는 도구가 하나라도 성공하면 턴 시작 전 상태를 `versions/page.<N>.json`으로 저장하고 `version`을 올린다(턴당 한 번).
- `edit_photo`는 항상 **업로드 원본**(`origin: "upload"`)에서 시작한다. 편집본을 다시 편집하지 않아 화질이 누적해 떨어지지 않는다.
- `add_section`으로 만든 `hero`·`scene`의 `photo`는 `p1`으로 시작한다.

### 4.3 되돌리기

UI의 "되돌리기" 버튼 → `POST /projects/{id}/undo` → 가장 최근 `versions/page.<N>.json`을 `page.json`으로 복원하고 그 파일을 지운다. 도구가 아니라 버튼이다. 사진 파일은 지우지 않는다.

## 5. 근거 규칙

- **근거 텍스트** = 상품명 + 설명(`notes`) + 사용자가 채팅에 쓴 모든 메시지. 사용자가 "350g이에요"라고 답하면 그 뒤로 350은 근거가 있다.
- **숫자 검사**: 새 문구(`headline`·`body`)와 근거 텍스트에서 정규식 `(\d+(?:[.,]\d+)?)\s*([a-zA-Z%℃°]+)?`로 `(숫자, 단위)` 쌍을 뽑는다(단위는 소문자로, 쉼표 소수점은 점으로 정규화). 새 문구의 쌍이 단위를 가지면 같은 쌍이, 단위가 없으면 같은 숫자가 근거에 있어야 한다. 부분 문자열 비교가 아니다: "5"는 "500ml"로, "500g"은 "500ml"로 근거가 생기지 않는다.
  `# ponytail: 단위는 라틴 문자·%·℃만 인식. "3시간"·"2인분" 같은 한글 단위는 숫자만 비교하며 주장 검사가 보완한다`
- **스펙 표 검사**: `spec_table`의 각 줄 값(`": "` 뒤)이 근거 텍스트에 공백을 무시하고 그대로 들어 있어야 한다.
- **주장 검사**: `update_text`·`add_section`의 새 문구는 숫자 검사를 통과한 뒤 문장마다 판정 호출을 거친다(한 문장이라도 걸리면 거절). 입력은 근거 텍스트 + 분석의 `visible_features`·`colors` + 새 문구, 출력은 `json_schema` `{"phrases": [{"phrase", "type": "fact"|"mood", "supported"}]}`이고, `type`이 `mood`가 아니면서 `supported`가 `true`가 아닌 구절이 근거 없는 주장이다. 계획 단계 실측(2026-10-16, base 9B, 8건): 걸러낼 목록만 내게 한 방식은 8건 중 2~3건(감성 문구까지 모두 걸러냄), 구절을 나눠 사실/분위기로 분류하는 방식은 thinking 끔으로 8건 중 7건·평균 0.9초, thinking 켬은 빈 응답 때문에 5/8이었다. 비어 있지 않으면 거절하고 `"오류: 근거 없는 주장 '냉 보온'. 빼거나 ask_user로 물어보세요."`를 모델에게 돌려준다. thinking 끔, `max_tokens` 800, 건당 1초 안팎. 판정 호출이 실패하면(서버 오류·JSON 깨짐) 한 번 다시 부르고, 또 실패하면 **반영하지 않는다**(fail-closed). 도구 결과는 `"오류: 주장 검사를 하지 못해 반영하지 않았습니다."`이고 채팅 답에도 그 사실이 남는다.
  - 근거: 스파이크에서 시스템 프롬프트가 "사용자 메모에 없는 사실은 지어내지 말라"고 명시했는데도 base/thinking off가 "따뜻하고 차갑게"(냉 보온, 입력에 없음)를 써 넣었다. 프롬프트만으로는 막히지 않는다.
  - 감성 표현("하루를 산뜻하게")은 사실이 아니므로 통과해야 한다. 판정 프롬프트에 이 구분과 예시를 넣는다.
- **판매자 주장은 근거다.** 사용자가 "식기세척기 사용 가능하다고 써줘"라고 하면 그 메시지가 근거 텍스트에 들어가므로 그대로 쓴다. 사실 여부의 책임은 판매자에게 있고, 에이전트가 막는 대상은 **모델이 스스로 덧붙인** 사실이다.
- 사진에서 보이는 것(색·모양)은 분석 결과가 근거다. 숫자 검사의 근거 텍스트에는 넣지 않는다(숫자가 거의 없다).

## 6. 사진 규칙

- 업로드 원본 파일은 덮어쓰지 않는다. 편집은 항상 새 파일이다.
- `hero`는 원본으로 시작한다. 사용자가 요청하면 편집본도 넣을 수 있다(미리보기로 확인하고 `use_photo`로 되돌린다).
- 편집 요청(실측으로 확인한 형식, `llm.py` 한 곳에만 둔다):
  ```json
  POST /v1/images/generations
  {"model": IMAGE_MODEL, "prompt": "<지시> 제품의 모양, 색상, 로고와 무늬는 그대로 보존한다.",
   "mode": "edit", "image": "<원본 raw base64, data URI 아님>",
   "size": "1024x1024", "steps": IMAGE_STEPS, "strength": 0.3, "response_format": "b64_json"}
  → {"data": [{"b64_json": "<PNG>"}]}
  ```
  건당 약 46~47초. 비정사각 원본(1263×892)도 1024×1024로 나오며 제품 전체가 보였다. 서버가 crop·pad·stretch 중 무엇을 했는지는 확인하지 못했다. 그래서 보내기 전에 EXIF 방향을 적용하고, 비율을 유지한 채 1024×1024 흰 캔버스 가운데에 넣는다(`ImageOps.contain` 후 붙이기). 구현 후 실측(2026-10-16): 1263×892 원본과 4:3·3:4로 자른 입력 모두에서 편집 결과에 제품이 늘어나거나 잘린 흔적이 육안으로 보이지 않았다(픽셀 단위 대조는 하지 않음). 흰 여백은 모델이 배경으로 채운다.
- 편집 결과는 큰 형태·색·무늬를 유지하지만 작은 무늬·장식은 원본과 같지 않다. 그래서 편집본은 연출용이며, 정확한 디테일은 원본 사진이 보여준다.

## 7. API

| 메서드·경로 | 입력 | 출력 |
|---|---|---|
| `GET /` | — | `static/index.html` |
| `POST /projects` | multipart: `photos`(여러 개), `name`, `notes` | `{id, version, messages}` (초안 완료 후 응답) |
| `GET /projects/{id}` | — | `{page, chat}` (새로고침 복원용) |
| `POST /projects/{id}/chat` | `{"text": "…"}` | `{reply, version}` (`reply.tools`에 도구 기록) |
| `POST /projects/{id}/undo` | — | `{version}`, 되돌릴 게 없으면 409 |
| `GET /projects/{id}/page` | — | 페이지 HTML (미리보기·캡처 공용) |
| `GET /projects/{id}/photos/{file}` | — | 사진 파일 |
| `POST /projects/{id}/export` | — | `{png, jpg, sections[]}` (파일 URL) |
| `GET /projects/{id}/export/{file}`, `GET /projects/{id}/export/sections/{file}` | — | 내보낸 파일 |

응답 형식(예시):

```jsonc
// POST /projects, POST /projects/{id}/chat 공통의 메시지 모양 = chat.json 항목
{"role": "assistant", "text": "초안을 만들었어요. 무게는 얼마인가요?",
 "tools": [{"name": "update_text", "args": {"section_id": "s1", "field": "headline", "text": "…"}, "ok": true, "result": "s1 headline 변경"}]}

// POST /projects → 200
{"id": "20261016-153012-ab12", "version": 1, "messages": [<assistant 메시지 1개>]}

// POST /projects/{id}/chat → 200
{"reply": <assistant 메시지>, "version": 4}

// POST /projects/{id}/export → 200 (URL은 서버 기준 경로)
{"png": "/projects/<id>/export/page.png", "jpg": "/projects/<id>/export/page.jpg",
 "sections": ["/projects/<id>/export/sections/01-hero.png", "…"]}

// 모든 오류 → 4xx/5xx
{"error": "사람이 읽을 한국어 문장"}
```

- 상태 코드: 입력 오류 400(필수 필드 누락 같은 요청 형식 오류도 422가 아니라 400), 없는 프로젝트 404, 되돌릴 것 없음 409, 모델 서버 오류 502. 다른 모델 작업이 진행 중이면 대기한다(단일 사용자이므로 409를 내지 않는다).
- 채팅에서 모델 서버 오류가 나면 502와 `error`를 돌려주고 `chat.json`에는 사용자 메시지만 남긴다.
- `GET /projects/{id}/photos/{file}`·`/export/…`는 `page.json`의 `photos[].file`에 있거나 `export/` 아래에서 서버가 만든 파일만 돌려준다. 이름은 `^[\w.-]+$`만 받고, 해석한 경로가 프로젝트 폴더 밖이면 404. MIME은 확장자로 정한다.
- 서버는 `127.0.0.1`에만 바인딩한다.
- UI는 프로젝트 id를 URL 해시(`/#<id>`)에 두어 새로고침해도 이어진다.
- 요청 처리 중에는 UI가 입력을 막고 스피너를 보인다.

## 8. 렌더링 (Codex 담당)

- `render.py`의 `render_html(page, photo_url) -> str` 하나가 미리보기와 캡처에 같이 쓰인다. 모델이 쓴 모든 문자열은 `html.escape`를 거친다.
- 폭 860px 고정, 섹션마다 `<section data-section-id="s1" data-kind="hero">`.
- 폰트는 시스템 폰트(`-apple-system, "Apple SD Gothic Neo", sans-serif`). 외부 CDN을 쓰지 않는다.
- 섹션 종류마다 디자인 1개. 변형·테마 선택은 범위 밖.
- 내보내기: Playwright(Chromium)로 `/projects/{id}/page`를 열어 전체 페이지 PNG → `page.png`, Pillow로 `page.jpg`(품질 90), 각 `section` 요소 캡처 → `sections/NN-<kind>.png`.

## 9. 실측으로 정한 기본값

Codex 스파이크(2026-10-16, `.orchestration/tasks/2026-10-16-mlx-probe.md`): 도구 6개, 요청 10개, 모델 2 × thinking on/off, temperature 0. 사람 판정.

| 설정 | 정답(브리프 기준) | 정답(§5 기준) | 평균 지연 | 주요 실패 |
|---|---:|---:|---:|---|
| base, thinking on | 7/10 | **8/10** | 1.72초 | #1 문구 다듬기를 되물음, #10 `max_tokens` 320에서 잘려 `text` 누락 |
| base, thinking off | 5/10 | 6/10 | 0.68초 | #1·#10 되물음, #6 도구 대신 일반 텍스트, #8 냉 보온을 지어냄 |
| run-1, thinking on | 5/10 | 6/10 | 1.23초 | #1·#10 되물음, #8 `ask_user`만, #9 "고마워"에 `ask_user` |
| run-1, thinking off | 6/10 | 7/10 | 0.35초 | #1 되물음, #8 사진 편집 누락, #9 `ask_user` |
| 합계 | base 12/20 · run-1 11/20 | base 14/20 · run-1 13/20 | | |

- 브리프 기준은 #7("식기세척기 써줘")을 그대로 쓰면 오답으로 셌다. §5의 "판매자 주장은 근거" 규칙으로는 #7이 네 설정 모두 정답이다.
- 스파이크는 도구 6개였다. 구현 후 7개 계약(`use_photo` 추가, 인자 변경)으로 `scripts/eval_tools.py` 11건을 재니 base/on **11/11**, 주장 검사 **8/8**, base/off 10/11·8/8이었다(2026-10-16, Codex 측정 후 Claude 재측정 동일).
- 도구 이름·인자 JSON 파싱 실패, 없는 도구·id 호출은 40건 중 0건이었다.
- 편집 중 텍스트 요청은 대기 없이 0.64초에 응답했다. mlx-serve RSS 최대 10.8GiB, 모델 상주 합계(9B + Qwen-Image 4bit) 약 22GiB.

| 항목 | 결정 | 근거 |
|---|---|---|
| `TEXT_MODEL` | base 9B (`empero-ai/Qwen3.8-9B-Distill-MLX-4bit`) | §5 기준 base 14/20 vs run-1 13/20. run-1은 잡담에도 `ask_user`를 부르고, v1 도구·공예 데이터로 학습해 v2 도구·일반 상품과 맞지 않는다. 차이가 1건이라 §11 재측정에서 뒤집히면 바꾼다 |
| thinking | 켬, 도구 루프 `max_tokens` 1,024 | §5 기준 base on 8/10 vs off 6/10. 320 상한에서 잘린 건이 있었다. 지연은 라운드당 2초 안팎이라 성공 기준(30초) 안이다 |
| 판정·초안 JSON 호출 | thinking 끔 | 분류·구조화 출력이라 추론이 필요 없고 빠르다. 구현 후 실측: 실제 초안 7건(스모크 3 + 직접 4)의 분석·문구 호출에서 JSON 재시도 0회 |
| 수정 루프 방식 | 도구 호출 유지 | base/on 8/10이 기준(7/10) 이상, 파싱 실패 0건 |
| 이미지 편집 | §6의 형식, 1024×1024, contain 패딩 | 실측 |
| JSON 응답 파싱 | `json_schema` 요청 + 응답의 ` ```json ` 펜스 제거 후 `json.loads` | 스키마 없이 요청한 비전 응답은 두 모델 모두 펜스로 감쌌다 |

## 10. 오류 처리

| 상황 | 동작 |
|---|---|
| MLX Serve 연결 실패·4xx·5xx | 페이지 변경 없음. 502 + `{"error": "모델 서버 오류: <응답 본문 앞 200자>"}` |
| 주장 검사 두 번 실패 | 수정은 반영하지 않음(fail-closed), 초안은 만들지 않음 |
| 초안 JSON 두 번 깨짐 | 초안을 만들지 않고 오류 표시. 업로드 사진은 남는다 |
| 사진 편집 실패 | 이전 사진 유지, 채팅에 실패 사실 표시 |
| 도구 인자 오류 | 모델에게 오류 문장으로 돌려줌(§4.2) |
| 도구 라운드 4회 초과 | 그때까지 반영분 유지, 실행한 도구 목록과 함께 종료 |

## 11. 테스트

- `tests/test_page.py`: 도구 7개(없는 id, 순서 변경, `use_photo`, 섹션 id 부여), 숫자·스펙 표 근거 검사(근거 없는 숫자 거절, 채팅 답변 후 허용), 버전 저장·되돌리기, HTML 이스케이프.
- `tests/test_agent.py`: 가짜 LLM 함수로 채팅 루프 — 도구 오류가 모델에게 돌아감, `ask_user`로 턴 종료, 4라운드 상한, 초안 JSON 재시도, 주장 검사가 근거 없는 구절을 내면 거절되고 판정 호출이 두 번 실패하면 반영하지 않음(fail-closed), 비전 응답의 코드 펜스 제거.
- `scripts/smoke.py`: 실제 MLX Serve로 사진 3장(정사각·가로·세로 각 1) → 초안 → 채팅 3건 → 내보내기. 단계별 시간, 분석·문구 `json_schema` 응답이 바로 파싱됐는지(재시도 횟수), 편집 결과의 비율 왜곡 여부를 기록.
- `scripts/eval_tools.py`: 도구 스키마와 시스템 프롬프트를 `pagegen`에서 가져와(복사본이 아니라) 실제 계약으로 잰다. 실모델 회귀 기준:
  - 도구: 스파이크 10건을 7개 도구 계약으로 옮기고 `use_photo`("원래 사진으로 되돌려") 1건을 더한 11건. base/on 9/11 이상, #1(짧게)·#10(감성적으로)은 되묻지 않고 고친다.
  - 주장 검사 8건: 거절 3건("하루 종일 따뜻하고 차갑게", 근거 없는 "식기세척기 사용 가능", "12시간 보냉 유지"), 통과 5건(사용자가 말한 뒤의 "식기세척기 사용 가능", 근거 있는 "이중 진공 단열로 따뜻함이 오래가요", 감성 문구 3개). 목표 8/8. 계획 단계 실측은 7/8("따뜻하고 차갑게"를 분위기로 판정).
  - 못 미치면 실패 사례로 프롬프트를 고쳐 다시 잰다. 모델·thinking 설정을 바꿀 때마다 다시 돌린다.

## 12. 작업 분담

| 누가 | 맡는 것 | 파일 |
|---|---|---|
| Claude (지휘) | 스펙·계획·핵심 로직·검수·커밋 | `pagegen/page.py`, `pagegen/agent.py`, `pagegen/llm.py`, `pagegen/server.py`, `tests/` |
| Codex (워커) | 렌더링·UI·실모델 검증 | `pagegen/render.py`, `static/index.html`, `scripts/smoke.py`, `scripts/eval_tools.py`, 측정 보고 |

- 둘이 공유하는 계약은 §3의 `page.json`과 §8의 `render_html(page, photo_url)` 시그니처뿐이다.
- Codex에게는 `.orchestration/tasks/<날짜>-<주제>.md` 브리프로 맡기고 터미널에는 한 줄만 보낸다. 결과는 Claude가 diff·테스트로 확인한 뒤 받는다. Codex는 git 상태를 바꾸지 않는다.
- `.orchestration/`, `projects/`, `.venv/`는 git에 넣지 않는다.
