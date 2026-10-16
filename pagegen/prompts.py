"""Prompts, JSON schemas and tool definitions. Task 9 tunes the wording against the real model (scripts/eval_tools.py)."""
import json

from .page import KINDS

CHAT_RULES = """너는 상세페이지 편집 에이전트다. 사용자의 요청을 도구로 처리하고, 끝나면 무엇을 바꿨는지 한두 문장의 한국어로 답한다.
규칙:
1. "짧게", "감성적으로", "강하게"처럼 문구를 다듬는 요청은 되묻지 말고 바로 update_text로 고친다. 섹션을 말하지 않으면 hero 섹션을 고친다.
2. 감사·인사·잡담에는 도구를 부르지 않고 한 문장으로 답한다.
3. 사용자가 말하지 않은 제품 사실(기능, 성능, 소재, 크기, 인증, 호환)은 새로 쓰지 않는다. 사용자가 대화에서 직접 말한 사실은 그대로 쓴다.
4. ask_user는 꼭 필요한 사실이 페이지와 대화 어디에도 없을 때만 쓴다. 문구 다듬기에는 쓰지 않는다.
5. 사진의 배경·장면을 바꾸는 요청은 edit_photo, 원래 사진으로 되돌리는 요청은 use_photo를 쓴다.
6. 도구 결과가 '오류:'로 시작하면 그 내용대로 고쳐 다시 시도하거나, 할 수 없는 이유를 사용자에게 알린다.
섹션 종류: hero(대표 사진+헤드라인), points(특징, 본문 한 줄에 '제목: 설명'), scene(연출 사진+설명), spec_table(제품 정보, 한 줄에 '항목: 값'), notice(관리·주의).
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
    _tool("update_text", "섹션의 헤드라인이나 본문을 새 문구로 바꾼다. 문구 다듬기 요청은 되묻지 말고 이 도구로 바로 고친다.",
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
    _tool("ask_user", "꼭 필요한 제품 사실(무게, 소재 등)이 페이지와 대화 어디에도 없을 때만 사용자에게 묻는다.",
          {"question": _S}),
]

JUDGE_SYSTEM = """새 문구를 짧은 구절로 나누고 각 구절을 분류해 JSON으로 답하라. "A하고 B"처럼 이어진 주장은 A와 B를 따로 나눈다.
type: "fact" = 제품의 기능·성능(보온·보냉·방수·내구성 등)·소재·크기·인증·호환·사용 가능 여부를 단정하는 구절. "mood" = 분위기·감정·장면·추천·꾸밈말.
supported: fact일 때만 따진다. 근거 텍스트나 사진에서 보이는 것에 같은 뜻이 있으면 true, 근거에 없는 성능·기능·소재면 false. mood는 항상 true.
예) 근거 '이중 진공 단열' → "따뜻함이 오래가요"는 fact/true(단열의 결과). "차가운 음료도 시원하게"는 fact/false(보냉은 근거에 없음)."""

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
- questions: 상세페이지에 필요한데 설명에 없는 정보를 묻는 질문, 최대 3개(예: "용량은 얼마인가요?")."""

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
- scene.photo_instruction은 이 제품이 잘 어울리는 배경이나 사용 장면을 한 문장으로 쓴다(예: "아침 햇살이 드는 원목 식탁 위")."""


def _object(**properties) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


_T = {"type": "string"}
COPY_SCHEMA = _object(
    hero=_object(headline=_T, body=_T),
    points=_object(headline=_T, items={"type": "array", "items": _object(title=_T, text=_T)}),
    scene=_object(headline=_T, body=_T, photo_instruction=_T),
    notice=_object(headline=_T, body=_T),
)
