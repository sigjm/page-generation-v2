# 주장 판정 파인튜닝 설계 (1단계)

- 작성: 2026-10-16
- 상태: 사용자 검토 대기
- 기반: `2026-10-16-conversational-detail-page-agent-design.md` §5(근거 규칙), `2026-10-16-section-layouts-design.md` §6
- 2단계(초안 문구 DPO)는 이 단계 결과를 보고 따로 설계한다.

## 1. 목표

주장 판정(`agent.judge`)은 페이지에 근거 없는 제품 사실이 들어가는 것을 막는 마지막 장치다. 지금 원본 9B는 실측에서 이런 일을 했다.

- 근거 없는 주장을 놓침: "습기나 물기에는 주의하세요", "튼튼한 금속 잠금장치", "집의 품격을 높여주는"
- 근거 있는 문장을 거름: 설명에 "전통 자개 공예"가 있는데 "공예 기법: 자개"를 근거 없음으로 판정
- 같은 구절을 반복하다 출력 한도에서 잘림(JSON이 깨져 판정 실패)

판정 한 가지만 Qwen3.8-9B LoRA로 학습해 이 세 가지를 줄인다.

### 합격 기준 (평가셋, §6)

| 지표 | 기준 |
|---|---|
| 근거 없는 주장 문장 잡기 F1 | 원본 9B보다 **10%p 이상** 높음 |
| 근거 있는·분위기 문장을 잘못 거르는 비율 | 원본 9B 이하 |
| 판정 실패율(JSON 두 번 실패) | 0% |
| `scripts/eval_tools.py` 판정 | 8/8 유지 |

못 미치면 채택하지 않고 원본을 계속 쓴다. 데이터를 고쳐 다시 학습하는 것은 사용자에게 결과를 보고한 뒤 정한다.

### 범위 밖

초안 문구·도구 호출 학습(2단계 이후), 이미지 입력 학습, 판정 형식 변경, 일반 지시 데이터 섞기(§5의 역할 분리로 필요 없다).

## 2. 결정 요약

| 항목 | 결정 | 이유 |
|---|---|---|
| 정답 교사 | Codex(`codex exec`, 비대화) 하나 | 사용자 선택. v1에서 개인 학습용으로 허용한 방식 |
| 교사 하나의 약점 보완 | 기계 검사로 이상한 정답 버림 + 평가셋은 Claude가 전부 검토 | 학습 정답의 잡음은 일부 견디지만 평가 정답은 틀리면 안 된다 |
| 입력 데이터 | Codex가 만든 가상 상품 + 원본 9B가 실제로 쓴 문구 | 저작권 문제 없음. 9B가 실제로 저지르는 실수가 그대로 담김 |
| 출력 형식 | 지금 런타임과 같은 `JUDGE_SCHEMA`(`{"phrases": [{phrase, type, supported}]}`), 공백 없는 JSON | 코드·프롬프트를 바꾸지 않고 모델만 바꿔 끼운다. 짧은 출력은 반복 루프도 줄인다 |
| 학습 도구 | mlx-vlm 0.7.8 LoRA, 텍스트 전용 행, 답만 학습 | v1에서 검증한 경로 |
| 적용 | `JUDGE_MODEL` 설정으로 판정 호출만 새 모델 | 문구·도구 호출은 원본 그대로라 망각 위험이 서비스에 닿지 않는다 |

## 3. 데이터

모든 데이터는 `.local/finetune/judge/`(git 제외)에 둔다. 사용자 데이터(`projects/`)는 쓰지 않는다.

### 3.1 가상 상품 (`products.jsonl`, Codex 생성)

5개 분류(의류·잡화, 식품, 화장품, 가전·전자, 생활·주방) × 60개 = 300개. 한 줄:

```json
{"id": "food-017", "category": "식품", "name": "제주 감귤 착즙 주스",
 "notes": "제주산 감귤 100% 착즙, 1L, 무가당. 냉장 보관, 개봉 후 3일 안에 드세요.",
 "visible_features": ["투명 유리병", "주황색 액체", "초록 뚜껑"], "colors": ["주황", "초록"]}
```

- `notes`는 판매자가 실제로 쓸 법한 2~5문장. 숫자·소재·관리법이 있는 상품과 거의 없는 상품을 섞는다(설명이 빈약한 상품 약 20%).
- 실존 브랜드·상표는 쓰지 않는다.

### 3.2 판정할 문장 (`sentences.jsonl`)

1. **9B 문장**(on-policy): 상품마다 원본 9B를 지금 쓰는 `COPY_SYSTEM`·`COPY_SCHEMA`로 두 번 부른다(temperature 0, 0.7). 분석 결과는 상품의 `category`·`visible_features`·`colors`로 만들고 `facts`·`questions`·`photo_captions`는 비운다. 나온 문구(hero·banner·points 줄·scene·notice·details)를 런타임과 같은 방식(`agent._sentences`)으로 문장으로 쪼갠다. 같은 상품 안에서 똑같은 문장은 하나만 남긴다.
2. **끼워 넣은 문장**(hard negative): Codex가 9B 문장 일부를 골라 근거 없는 제품 사실(기능·성능·소재·인증·관리법·용도)을 하나 끼워 넣은 변형을 만든다. 최종 데이터에서 근거 없는 주장이 든 문장이 35~50%가 되게 양을 맞춘다.
3. 한 줄: `{"id", "product_id", "text", "source": "9b-t0" | "9b-t07" | "injected"}`.

### 3.3 정답 (`labels.jsonl`, Codex)

- `codex exec`를 일괄로 불러 문장마다 `JUDGE_SCHEMA` 형식 정답을 받는다. 지시문은 런타임 `JUDGE_SYSTEM`과 같은 기준에 정답 매기기 규칙을 더한 것이다(분위기 표현은 `mood`, 근거 텍스트·사진에서 보이는 것에 같은 뜻이 있으면 `supported`, 애매하면 `false`, 사용자 입력에 있는 주장은 근거로 본다).
- 한 번에 문장 20개씩, 상품의 근거(`basis_of` 형식)와 함께 보낸다.
- **기계 검사**(통과 못 하면 버림): 형식이 스키마와 맞음, 모든 `phrase`가 문장 안에 공백 무시하고 들어 있음, `phrases`가 비어 있지 않음, 같은 구절 중복 없음. 끼워 넣은 문장은 끼운 구절이 `fact`/`false`로 표시돼야 한다.

### 3.4 나누기

- **평가셋**: 분류마다 6개 상품(총 30개)을 먼저 떼어 둔다. 그 상품의 문장에서 근거 없음·있음을 반반 섞어 150문장을 뽑고, **Claude가 150문장 정답을 전부 검토해 고친다.** 이 상품들은 학습에 쓰지 않는다.
- 나머지 270개 상품의 문장을 상품 해시로 학습 90% / 검증 10%.

## 4. 학습

- 환경: `uv venv -p 3.13 .local/venvs/mlx-vlm && uv pip install mlx-vlm==0.7.8 torch torchvision`(v1과 같음). 앱 의존성(`pyproject.toml`)에는 넣지 않는다.
- 행 형식(텍스트 전용): `{"id", "messages": [system: JUDGE_SYSTEM, user: "{basis}\n새 문구: {text}", assistant: 공백 없는 정답 JSON]}`. user 내용은 런타임 `agent.judge`가 보내는 것과 글자 하나 다르지 않게 같은 함수로 만든다.
- 명령: `python -m mlx_vlm.train --model ~/.mlx-serve/models/empero-ai/Qwen3.8-9B-Distill-MLX-4bit --data .local/finetune/judge/sft --train-on-completions --grad-checkpoint --max-seq-length 2048 --output-path .local/finetune/judge/lora/r1`. LoRA rank 16, alpha 32, 학습률 5e-5(v1의 절반 — 짧고 좁은 과제), 1 epoch.
- 판정 행은 짧아 학습 메모리가 v1 도구 행(19.4GB) 이하로 본다. MLX Serve를 켜 둔 채 학습하고, 첫 50스텝의 최대 메모리를 재서 64GB 안에서 여유(8GB 이상)가 없으면 멈추고 사용자에게 알린다.
- 병합: v1 `fuse_lora.py`를 가져와 `~/.mlx-serve/models/local/Qwen3.8-9B-pagegen-judge-r1`을 만든다(4bit, 약 5.6GB).

## 5. 적용

- `pagegen/llm.py`에 `JUDGE_MODEL`(환경 변수, 기본값 `TEXT_MODEL`)을 두고, `chat`·`chat_json`에 `model` 인자를 더한다. `agent.judge`만 `JUDGE_MODEL`로 부른다. 설정하지 않으면 지금과 똑같이 동작한다.
- MLX Serve는 시작할 때 모델 폴더를 읽으므로, 새 모델을 쓰려면 **MLX Serve 재시작이 한 번 필요하다. 재시작 전에 사용자에게 묻는다.**
- 메모리: 판정 모델 5.5GB가 더 상주한다(원본 9B + 판정 9B + Qwen-Image 4bit ≈ 28GB).

## 6. 평가 (`scripts/finetune/eval_judge.py`)

> LangSmith 적용 스펙(`2026-10-16-langchain-adoption-design.md` §4)에 따라, API 키가 있으면 같은 평가를 LangSmith 데이터셋·실험으로도 기록한다. 아래 로컬 지표와 합격 기준은 그대로다.

- 입력: 평가셋 150문장(Claude 검토 정답), `--model`.
- 문장 단위로 런타임과 같은 `agent.judge`를 부른다(같은 프롬프트·스키마·max_tokens·재시도).
- 지표: 근거 없는 문장 정밀도·재현율·F1, 근거 있는·분위기 문장을 거른 비율, 판정 실패(LLMError) 수, 평균 지연.
- 출력: 표 + 틀린 문장 원문 목록, `--out`에 JSON.
- 원본 9B와 r1을 같은 평가셋으로 각각 돌려 비교한다. 그 뒤 `scripts/eval_tools.py`(판정 8건)를 `JUDGE_MODEL=r1`로 한 번 돌린다.

## 7. 분담

| 누가 | 맡는 것 |
|---|---|
| Codex | 가상 상품 300개 생성, 끼워 넣은 문장 생성, `codex exec` 정답 매기기 스크립트 작성·실행 |
| Claude | `JUDGE_MODEL` 코드, 9B 문장 생성·SFT 변환·평가 스크립트, 평가셋 150문장 검토, 학습·병합 실행, 결과 판정·커밋 |

## 8. 위험과 대응

| 위험 | 대응 |
|---|---|
| Codex 정답이 기준과 어긋남 | 기계 검사 + 평가셋 전수 검토. 검토에서 Codex 정답 오류율이 10%를 넘으면 정답 지시문을 고쳐 다시 매긴다 |
| 근거 없음 쪽으로 쏠린 학습(과하게 거름) | 근거 없음 비율 35~50%로 맞추고, 잘못 거르는 비율을 합격 기준에 둔다 |
| v1 run-2처럼 빈약한 정답 학습 | `phrases`가 빈 정답은 버리고, 정답 길이 분포를 원본 9B 출력과 비교해 보고한다 |
| 학습 메모리 부족 | 첫 50스텝 측정 후 멈춤 규칙(§4) |
