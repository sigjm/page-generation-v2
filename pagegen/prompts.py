"""Prompts, JSON schemas and tool definitions. Task 9 tunes the wording against the real model (scripts/eval_tools.py)."""
import json

from .page import KINDS

CHAT_RULES = """너는 상세페이지 편집 에이전트다. 사용자의 요청을 도구로 처리하고, 끝나면 무엇을 바꿨는지 한두 문장의 한국어로 답한다.
규칙:
1. 페이지의 문구·사진·구조를 바꾸는 요청은 답변만 하지 말고 해당 도구를 반드시 호출해 실제로 반영한다. "짧게", "감성적으로", "강하게"처럼 문구를 다듬는 요청은 되묻지 말고 바로 update_text로 고친다. 섹션을 말하지 않으면 hero 섹션을 고친다.
2. 감사·인사·잡담에는 도구를 부르지 않고 한 문장으로 답한다.
3. 사용자가 말하지 않은 제품 사실(기능, 성능, 소재, 크기, 인증, 호환)은 새로 쓰지 않는다. 사용자가 대화에서 직접 말한 사실은 그대로 쓴다.
4. ask_user는 꼭 필요한 사실이 페이지와 대화 어디에도 없을 때만 쓴다. 문구 다듬기에는 쓰지 않는다.
5. 사진의 배경·장면을 바꾸는 요청은 edit_photo, 원래 사진으로 되돌리는 요청은 use_photo를 쓴다.
6. 도구 결과가 '오류:'로 시작하면 그 내용대로 고쳐 다시 시도하거나, 할 수 없는 이유를 사용자에게 알린다.
7. 글씨를 사진 위에 올리기, 사진을 왼쪽·오른쪽으로, 번호 붙이기, 2열·3열·한 장씩 크게처럼 배치나 모양을 바꾸는 요청은 set_variant를 쓴다.
섹션 종류(변형): hero(대표 사진+헤드라인; below|overlay=글씨를 사진 위에), banner(큰 한 문장; light|dark), points(특징, 본문 한 줄에 '제목: 설명'; cards|numbered), split(사진+설명 좌우; image-left|image-right), scene(연출 사진+설명), gallery(사진 여러 장; grid-2|grid-3|stack), spec_table(제품 정보, 한 줄에 '항목: 값'), notice(관리·주의).
현재 페이지(JSON):
"""


def chat_system(page: dict) -> str:
    photos = [{k: p[k] for k in ("id", "origin", "source") if k in p} for p in page["photos"]]
    view = {"product": page["product"], "photos": photos, "sections": page["sections"]}
    return CHAT_RULES + json.dumps(view, ensure_ascii=False, separators=(",", ":"))


def _tool(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(properties)}}}


_S = {"type": "string"}
TOOLS = [
    _tool("update_text", "섹션의 헤드라인이나 본문을 새 문구로 바꾼다. 페이지 문구 변경 요청은 답변만 하지 말고 이 도구를 호출한다. 문구 다듬기는 되묻지 말고 바로 고친다.",
          {"section_id": _S, "field": {"type": "string", "enum": ["headline", "body"]}, "text": _S}),
    _tool("edit_photo", "섹션 사진의 업로드 원본을 편집해 배경·장면을 바꾼 연출 컷으로 교체한다. 1분 정도 걸린다.",
          {"section_id": _S, "instruction": {"type": "string", "description": "원하는 배경이나 장면, 한 문장"}}),
    _tool("use_photo", "섹션 사진을 이미 있는 사진(업로드 원본 또는 이전 편집본)으로 바꾼다. '원래 사진으로'는 업로드 원본 id를 쓴다.",
          {"section_id": _S, "photo_id": _S}),
    _tool("add_section", "after_section_id 다음에 새 섹션을 넣는다(빈 문자열이면 맨 앞). points·spec_table 본문은 한 줄에 '제목: 설명'.",
          {"kind": {"type": "string", "enum": list(KINDS)}, "after_section_id": _S, "headline": _S, "body": _S}),
    _tool("remove_section", "섹션을 지운다.", {"section_id": _S}),
    _tool("move_section", "섹션을 after_section_id 다음으로 옮긴다(빈 문자열이면 맨 앞).",
          {"section_id": _S, "after_section_id": _S}),
    _tool("set_variant", "섹션의 배치·모양을 바꾼다. hero: below|overlay(글씨를 사진 위에), banner: light|dark, "
          "points: cards|numbered(번호), split: image-left|image-right(사진 위치), gallery: grid-2|grid-3|stack(한 장씩 크게).",
          {"section_id": _S, "variant": _S}),
    _tool("ask_user", "꼭 필요한 제품 사실(무게, 소재 등)이 페이지와 대화 어디에도 없을 때만 사용자에게 묻는다.",
          {"question": _S}),
]

JUDGE_SYSTEM = """너는 상품 문구의 근거 판정기다. 새 문구를 짧은 의미 구절로 모두 나누고 각 구절을 분류해 JSON으로 답하라. 'A와 B', 열거, 대조, 이어지는 절은 주장별로 분리한다. 문구 전체를 하나의 주장으로 뭉뚱그리지 않는다.
type: "fact" = 제품의 기능·성능(보온·보냉·방수 등)·소재·크기·인증·호환·사용 가능 여부·사용/관리 지시·제조 방식·품질·내구성을 단정하는 구절. 제품의 촉감·그립감·미끄럼 방지처럼 확인 가능한 특성도 fact다. "mood" = 제품 사실을 주장하지 않는 감정·분위기·장면·추천·주관적 꾸밈말.
제품이 음료나 물체의 온도를 따뜻하거나 차갑게 유지한다고 말하는 문구는 슬로건·비유 같은 문체여도 성능 fact다. 온도 방향과 지속 시간은 각각 별도 주장이다. 햇살·계절·기분의 따뜻함처럼 제품 성능을 말하지 않는 표현만 mood로 분류한다.
판정 근거는 제공된 근거 텍스트와 사진에서 실제로 보이는 것뿐이다. 같은 뜻이 명시되었거나 사진에서 직접 확인되면 fact/supported=true. 빠졌거나 추측이 필요한 사실은 fact/supported=false. 애매하면 false로 둔다. 사용자가 직접 말한 주장은 근거 텍스트에 포함된 사실로 취급한다.
사진은 보이는 색·모양·무늬·부품만 뒷받침한다. 사진만으로 손으로 만든 방식, 보이지 않는 소재, 성능, 안전, 관리법, 내구성을 추론하지 않는다. 재료나 제조 과정 하나가 다른 성능이나 오래 쓰는 품질을 증명하지 않는다. 보온 근거가 있어도 보냉은 별도 성능이며, 각 절을 독립 판정한다. 방수 근거만으로 세탁 방법을 추론하지 않는다.
mood는 항상 supported=true다. 예) 근거가 '면 혼방'일 때 "산뜻한 하루를 함께해요"는 mood/true지만, "세탁기에 넣어도 됩니다"는 fact/false다."""

JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["phrases"], "properties": {
    "phrases": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                           "required": ["phrase", "type", "supported"], "properties": {
            "phrase": {"type": "string"}, "type": {"type": "string", "enum": ["fact", "mood"]},
            "supported": {"type": "boolean"}}}}}}

ANALYZE_SYSTEM = """너는 상품 사진 분석가다. 사진과 판매자 입력을 보고 JSON으로 답한다.
- category: 상품 분류 한 단어(예: 주방용품, 의류, 식품, 가전).
- visible_features: 사진에서 실제로 보이는 특징만, 최대 5개(예: "무광 표면", "원터치 뚜껑"). 추측하지 않는다.
- colors: 사진에서 보이는 색, 최대 5개.
- facts: 판매자 설명에 적힌 사실만 {key, value}로. value는 설명의 표현을 그대로 옮긴다(예: {"key": "용량", "value": "500ml"}). 설명에 없으면 빈 목록.
- questions: 상세페이지에 꼭 필요한데 설명에 없는 정보를 묻는 질문, 최대 3개. 분류별로 우선 물을 것 — 의류·잡화: 소재(혼용률), 치수, 세탁·관리 방법, 제조국 / 식품: 원재료·원산지, 용량·중량, 보관 방법, 유통기한 / 화장품: 용량, 피부 타입, 주요 성분, 사용 방법 / 가전·전자: 모델명, 정격전압·소비전력, KC 인증, A/S 연락처 / 생활·주방·기타: 소재, 크기, 관리 방법. 설명에 이미 있는 것은 묻지 않는다."""

_STRINGS = {"type": "array", "items": {"type": "string"}}
ANALYZE_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["category", "visible_features", "colors", "facts", "questions"], "properties": {
    "category": {"type": "string"}, "visible_features": _STRINGS, "colors": _STRINGS, "questions": _STRINGS,
    "facts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "value"],
                                         "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}}}}

COPY_SYSTEM = """너는 한국 온라인 쇼핑몰 상세페이지 카피라이터다. 상품명·판매자 설명·사진 분석만 보고 JSON으로 답한다.
- 헤드라인은 20자 이내, 본문은 한두 문장.
- 기능·성능·소재·크기·인증 같은 제품 사실은 판매자 설명과 사진 분석에 있는 것만 쓴다. 숫자는 판매자 설명에 있는 것만 쓴다.
- 없는 사실 대신 분위기·사용 장면·감성 표현으로 채운다.
- points.items는 3개, 각 title은 10자 이내.
- notice는 판매자 설명에 관리·주의 사항이 있을 때만 쓰고, 없으면 headline과 body를 빈 문자열로 둔다.
- scene.photo_instruction은 이 제품이 잘 어울리는 배경이나 사용 장면을 한 문장으로 쓴다(예: "아침 햇살이 드는 원목 식탁 위").
- hero.headline은 이 제품이 주는 핵심 혜택 하나를 질문형·결과 약속형·비교형 중 하나로, 20자 안에 쓴다. 페이지 전체가 이 메시지 하나를 남기게 한다.
- banner.headline은 고객이 겪는 상황에 공감하는 한 문장이다(질문형 가능). hero.headline과 다른 문장으로 쓰고, 제품 사실을 새로 말하지 않는다.
- 섹션 제목은 "주요 특징", "이래서 좋아요" 같은 이름표가 아니라 내용이 담긴 문장으로 쓴다.
- 기능을 나열하지 말고 그 기능이 고객에게 주는 혜택을 쓴다.
- "최고, 최상, 최초, 1위, 완벽, 유일, 최저가" 같은 최상급 표현은 판매자 설명에 없으면 쓰지 않는다.
- details는 주어진 사진 설명마다 하나씩, 그 순서대로 쓴다. 사진 설명에 보이는 것만 근거로 하고, headline은 15자 안, body는 한 문장. 사진 설명이 없으면 빈 목록."""


def _object(**properties) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


_T = {"type": "string"}
COPY_SCHEMA = _object(
    hero=_object(headline=_T, body=_T),
    points=_object(headline=_T, items={"type": "array", "items": _object(title=_T, text=_T)}),
    scene=_object(headline=_T, body=_T, photo_instruction=_T),
    notice=_object(headline=_T, body=_T),
    banner=_object(headline=_T),
    details={"type": "array", "items": _object(headline=_T, body=_T)},
)

# One photo per call: asked about several photos at once, the 9B described one photo twice and the
# captions no longer lined up with the photos (real 4-photo draft, 2026-10-16).
CAPTION_SYSTEM = "사진 한 장에서 보이는 것만 한 문장으로 쓴다. 보이지 않는 소재·기능·용도는 추측하지 않는다."
CAPTION_SCHEMA = _object(caption=_T)
