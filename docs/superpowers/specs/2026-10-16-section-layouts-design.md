# 섹션 레이아웃 확장 설계 (리서치 반영)

- 작성: 2026-10-16
- 상태: 사용자 검토 대기
- 기반 스펙: `2026-10-16-conversational-detail-page-agent-design.md` (이하 "기본 스펙"). 이 문서는 기본 스펙의 §3(page.json), §4(처리 흐름), §5(근거 규칙), §8(렌더링)을 넓힌다. 적지 않은 것은 기본 스펙을 따른다.

## 1. 목표

지금은 업로드한 첫 사진만 페이지에 들어가고, 섹션 디자인이 종류마다 하나뿐이다. 국내 상세페이지 가이드를 조사해 그 구성 원칙을 섹션 종류·변형·배치 규칙·문구 규칙으로 옮긴다. 모델 가중치는 바꾸지 않는다(파인튜닝 없음).

### 성공 기준

- 사진을 4장 올리면 첫 초안에 4장이 모두 들어간다(대표·split 2개·갤러리).
- 채팅으로 "히어로 글씨를 사진 위에", "갤러리 3열로" 같은 레이아웃 요청이 반영된다.
- 판매자 설명에 없는 최상급 표현("최고", "1위" 등)은 페이지에 들어가지 않는다.
- 기존 성공 기준(초안 ≤120초, 입력에 없는 숫자 0건 등)은 그대로 지킨다.

### 범위 밖

후기·인증·구매 유도(CTA) 섹션(판매자 자료가 없으면 지어낼 수밖에 없다), 갤러리 사진 한 장씩 고르기, 페이지 전체 테마·템플릿 전환, 파인튜닝.

## 2. 리서치 요약 (2026-10-16)

국내 상세페이지 가이드 8곳 이상을 읽고 공통된 것만 옮겼다(출처는 §9).

| 원칙 | 이 설계에서 |
|---|---|
| 흐름: 첫 화면(대표 사진+핵심 가치 한 줄, 3초) → 문제 공감 → 핵심 강점 3가지 → 근거·디테일 컷 → 신뢰 → 스펙 → 배송·교환·주의 → 구매 유도 | 섹션 순서(§4). 신뢰·구매 유도는 범위 밖 |
| 헤드라인은 질문형·결과 약속형·비교형, 두 줄 이내, 기능보다 혜택 | 문구 프롬프트 규칙(§5) |
| "주요 특징" 같은 이름표형 제목보다 내용이 담긴 문장 | 문구 프롬프트 규칙 |
| 페이지 전체가 메시지 하나를 남긴다 | 문구 프롬프트 규칙 |
| 강점은 3개로 좁힌다 | points 3개(기존과 같음) |
| "최고·최상" 같은 최상급 표현은 업종에 따라 법적 위험 | 최상급 표현 기계 검사(§6) |
| 860px, 모바일 기준 큰 글자·넉넉한 여백, 읽는 페이지가 아니라 보는 페이지 | 렌더링(§7) |
| 품목별 필수 정보(상품정보제공 고시) | 분석의 빠진 정보 질문(§5) |

## 3. 섹션 종류와 변형

`page.json`(기본 스펙 §3)에 바뀌는 것:

- `kind`가 8종이 된다: `hero`, `banner`, `points`, `split`, `scene`, `gallery`, `spec_table`, `notice`.
- 섹션에 `variant` 필드가 생긴다(선택). 없으면 그 종류의 첫 변형으로 그린다. 변형이 없는 종류는 `variant`를 두지 않는다.
- `gallery`는 `photo` 대신 `photos: ["p2", "p3", …]`를 가진다. 비어 있을 수 없다.
- 사진 한 장을 가지는 종류(`PHOTO_KINDS`)는 `hero`, `scene`, `split`.
- 기존 page.json(변형 없음, 5종)은 그대로 읽히고 그려진다.

| kind | 변형 (첫 값이 기본) | 내용 |
|---|---|---|
| `hero` | `below`, `overlay` | 대표 사진 + 헤드라인 + 본문. `overlay`는 헤드라인을 사진 아래쪽 위에 얹는다 |
| `banner` | `light`, `dark` | 큰 한 문장(헤드라인). 본문은 선택(작은 글씨). 사진 없음 |
| `points` | `cards`, `numbered` | 본문 한 줄에 `제목: 설명`. `numbered`는 01·02·03 큰 번호 |
| `split` | `image-left`, `image-right` | 사진 반 + 헤드라인·본문 반 |
| `scene` | — | 연출 컷 + 헤드라인·본문 (기존) |
| `gallery` | `grid-2`, `grid-3`, `stack` | 사진 여러 장. `stack`은 한 장씩 폭 전체 |
| `spec_table` | — | 기존 |
| `notice` | — | 기존 |

```json
{"id": "s2", "kind": "banner", "variant": "light", "headline": "아침마다 식은 커피, 아쉽지 않으셨나요?", "body": ""}
{"id": "s4", "kind": "split", "variant": "image-left", "headline": "한 손으로 여닫는 뚜껑", "body": "…", "photo": "p2"}
{"id": "s7", "kind": "gallery", "variant": "grid-2", "headline": "더 많은 모습", "body": "", "photos": ["p4", "p5"]}
```

## 4. 첫 초안 조립 (기본 스펙 §4.1 확장)

1. **분석**에 `photo_captions: [str]`를 더한다. 모델에 보낸 사진(최대 4장) 순서대로, 사진에서 **보이는 것만** 한 문장씩.
2. **split 사진**은 `p2`, `p3`(있는 만큼, 최대 2장). **갤러리 사진**은 `p4` 이후 업로드 원본.
3. **문구**에 `banner{headline}`와 `details[{headline, body}]`를 더한다. `details`는 split 사진마다 하나씩, 그 사진의 `photo_captions`를 근거로 쓴다(입력에 "사진 2: <설명>" 형식으로 준다). split 사진이 없으면 빈 목록.
4. **섹션 순서**(사진이 없는 섹션은 빠진다):

   | 순서 | 섹션 | 변형 | 사진 |
   |---|---|---|---|
   | 1 | hero | `below` | `p1` 원본 |
   | 2 | banner | `light` | — |
   | 3 | points | `cards` | — |
   | 4 | split | `image-left` | `p2` |
   | 5 | split | `image-right` | `p3` |
   | 6 | scene | — | `p1` 편집본(기존과 같음) |
   | 7 | gallery | 2장 이상 `grid-2`, 1장 `stack` | `p4…` |
   | 8 | spec_table | — | — |
   | 9 | notice | — | — |

   사진이 1장이면 지금과 같은 구성에 banner만 더해진다.
5. **초안 검사**(기본 스펙 §4.1-6)는 banner·split에도 같다. 다른 점:
   - `hero`의 헤드라인이 걸리면 지금처럼 상품명으로 바꾼다. `banner`·`split`의 헤드라인이 걸리면 빈 문자열로 둔다(대표 제목을 되풀이하지 않는다).
   - 내용이 남지 않은 섹션은 뺀다: `banner`는 헤드라인이 비면, `split`은 헤드라인과 본문이 모두 비면, `gallery`는 사진이 없으면. 빠진 `split`의 사진은 갤러리 앞쪽에 넣는다(사진은 버리지 않는다).
   - 갤러리 제목 "더 많은 모습"은 고정 문구라 검사하지 않는다.

## 5. 문구·분석 프롬프트 규칙

`COPY_SYSTEM`에 더한다:

- 대표 헤드라인은 질문형·결과 약속형·비교형 중 하나로, 이 제품이 주는 핵심 혜택 하나를 20자 안에.
- 배너는 고객이 겪는 상황에 공감하는 한 문장(질문형 가능). 제품 사실을 새로 말하지 않는다.
- 모델이 쓰는 모든 섹션 제목은 "주요 특징", "이래서 좋아요" 같은 이름표가 아니라 내용이 담긴 문장으로. (코드가 넣는 고정 제목 — 갤러리 "더 많은 모습", 스펙 표 "제품 정보" — 은 예외)
- 기능을 나열하지 말고 그 기능이 고객에게 주는 혜택을 쓴다(사실은 기존 규칙대로 설명·분석에 있는 것만).
- 페이지 전체가 하나의 메시지(대표 헤드라인의 혜택)를 남기도록.
- "최고, 최상, 최초, 1위, 완벽, 유일, 최저가" 같은 최상급 표현은 판매자 설명에 없으면 쓰지 않는다.
- `details`는 주어진 사진 설명에 보이는 것만 근거로 쓴다.

`ANALYZE_SYSTEM`의 `questions`를 품목별 필수 정보(상품정보제공 고시) 중심으로 바꾼다:

| 분류 | 우선 물을 정보 |
|---|---|
| 의류·잡화 | 소재(혼용률), 치수, 색상, 세탁·관리 방법, 제조국 |
| 식품 | 원재료·원산지, 용량·중량, 보관 방법, 유통기한 |
| 화장품 | 용량, 피부 타입, 주요 성분, 사용 방법 |
| 가전·전자 | 모델명, 정격전압·소비전력, KC 인증, A/S 연락처 |
| 생활·주방·기타 | 소재, 크기, 관리 방법 |

이미 설명에 있는 정보는 묻지 않는다(기존 규칙).

## 6. 근거 규칙 추가: 최상급 표현 검사

- `SUPERLATIVES = ("최고", "최상", "최초", "1위", "완벽", "유일", "최저가", "넘버원", "No.1")`.
- 새 문구에 이 말이 있는데 근거 텍스트(기본 스펙 §5)에 없으면 거절한다: `"오류: 근거 없는 최상급 표현 '최고'. 빼고 다시 쓰세요."`. 대소문자는 무시한다.
- 숫자 검사와 같은 자리에서 돈다(`page.check_text`, 초안 검사의 문장별 통과 조건). 판정 호출보다 먼저라 모델 호출이 늘지 않는다.
- `# ponytail: 고정 낱말 목록. "가장 좋은" 같은 우회 표현은 주장 검사가 맡는다`

## 7. 채팅 도구 (기본 스펙 §4.2 확장)

- 새 도구 `set_variant(section_id, variant)` → 도구 8개. 그 종류에 없는 변형이면 `"오류: hero 변형은 below, overlay 중 하나입니다."`, 변형이 없는 종류면 `"오류: scene 섹션은 변형이 없습니다."`.
- `add_section(kind, …)`:
  - `split`: `photo`는 `p1`이 아닌 첫 업로드(없으면 `p1`), `variant`는 `image-left`.
  - `gallery`: `photos`는 `p1`을 뺀 업로드 원본 전부. 없으면 `"오류: 갤러리에 넣을 추가 사진이 없습니다."`.
  - `banner`: 사진 없음.
  - 변형이 있는 종류는 기본 변형으로 만든다.
- `use_photo`·`edit_photo`는 `split`에도 된다(`PHOTO_KINDS`). `gallery`에 쓰면 `"오류: gallery 섹션은 사진을 하나씩 바꿀 수 없습니다. remove_section 후 add_section(gallery)를 쓰세요."`.
- `update_text`·`remove_section`·`move_section`은 모든 종류에 된다.
- `CHAT_RULES`의 섹션 설명을 8종과 변형으로 바꾸고, "글씨를 사진 위에", "3열로", "번호로", "사진을 오른쪽에" 같은 배치 요청은 `set_variant`로 처리하라는 규칙을 더한다.

## 8. 렌더링 (Codex 담당, 기본 스펙 §8 확장)

- `<section data-section-id data-kind data-variant>`(변형이 없으면 `data-variant` 없음). 변형별 디자인은 CSS가 `data-variant`로 고른다.
- `hero/overlay`: 사진 위 아래쪽에 어두운 그라데이션과 흰 헤드라인·본문.
- `banner/light`: 밝은 배경에 큰 문장(32px 이상) 가운데. `banner/dark`: 짙은 배경(`#121B29` 계열)에 흰 글씨.
- `points/numbered`: 항목마다 큰 번호(01, 02, 03)와 제목·설명.
- `split`: 사진 50% + 글 50% 가로 배치. `image-right`는 좌우를 바꾼다. 사진은 잘리지 않게(`object-fit: contain`) 정사각 칸에.
- `gallery`: `grid-2`·`grid-3`는 정사각 칸 격자(사진 `contain`, 연한 배경), `stack`은 한 장씩 폭 전체. 사진이 1장인데 `grid-*`이면 폭 전체로 그린다.
- 모바일 가독성: 본문 17px 이상, 섹션 사이 여백 64px 이상(리서치 반영).
- 외부 리소스 없음, 모델 문자열 이스케이프(기존 규칙).

## 9. 테스트와 실측

- `tests/test_page.py`: 변형 검사(`set_variant`), 8종 `add_section` 기본값(split 사진·gallery 사진·변형), 갤러리에 `use_photo` 거절, 최상급 표현 검사(근거 있으면 허용).
- `tests/test_agent.py`: 사진 1·3·5장 초안 조립(섹션 순서, 변형, split·gallery 사진), banner·split 헤드라인이 걸리면 빈 문자열, 내용 없는 split의 사진이 갤러리로 감, 최상급 표현 문장 버림, `set_variant` 도구 경로.
- `tests/test_render.py`: 변형별 `data-variant`, split 좌우, gallery 격자·stack·1장, banner, 기존 5종 page.json 그대로 렌더.
- `scripts/eval_tools.py`: 사례 2건 추가("히어로 글씨를 사진 위에 올려줘" → `set_variant(s1, overlay)`, "갤러리를 3열로 바꿔줘" → `set_variant(<gallery id>, grid-3)`). 기준: 도구 ≥ 11/13, 주장 검사 8/8 유지.
- `scripts/smoke.py`: 사진 4장 초안 1건 추가. 초안 시간 ≤120초, 4장 모두 페이지에 있음, 내보낸 PNG 육안 확인.

### 분담

| 누가 | 맡는 것 |
|---|---|
| Claude | `page.py`, `agent.py`, `prompts.py`(구조·규칙), `tests/test_page.py`, `tests/test_agent.py`, 스펙·검수·커밋 |
| Codex | `render.py` + `tests/test_render.py`, `scripts/eval_tools.py` 사례 추가와 재측정, `scripts/smoke.py` 사진 4장 실행 |

### 리서치 출처

- 컨버타, 스마트스토어 상세페이지 만들기: https://converta.co.kr/community/resources/smartstore-detail-page-guide
- 후커블, 상세페이지 기획안 한 장으로 구성 잡는 법: https://www.hookable.ai/blog/detail-page-planning-brief
- 아이보스, 상세페이지 기획과 제작: https://www.i-boss.co.kr/ab-6141-64000
- 아이보스, 상세페이지 카피라이팅 실전 공식: https://www.i-boss.co.kr/ab-6141-69823
- 웹비스타, 헤드라인 쓰는 공식: https://webvista.co.kr/resources/webguide_view.php?no=771
- 토스페이먼츠, 쇼핑몰 광고 카피 작성 노하우: https://www.tosspayments.com/blog/articles/semo-110
- 고도몰, 전자상거래 상품정보제공 고시: http://guide.godo.co.kr/guide/php/information.by.goods/information.by.goods.htm
- 크몽, 상세페이지 디자인 총정리: https://kmong.com/article/582
- 플로우웍스, 잘 팔리는 상세페이지 디자인: https://flowworks.io/blog/상세페이지-디자인
- Adobe, 구매를 유도하는 상세페이지 제작 가이드: https://www.adobe.com/kr/creativecloud/roc/blog/photography/product-photo-detail-page-design.html
