"""Real-model regression for the chat tools and claim judge (spec §11). Needs MLX Serve at MLX_URL.

    uv run python scripts/eval_tools.py            # thinking on (the shipped setting)
    uv run python scripts/eval_tools.py --thinking off
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pagegen
from pagegen import agent, llm, prompts

pagegen.enable_dev_tracing("pagegen-dev")  # dev data only: traces go to LangSmith when .env has a key

PAGE = {
    "product": {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종"},
    "analysis": {"visible_features": ["무광 스테인리스 표면", "원터치 뚜껑"], "colors": ["실버"]},
    "photos": [
        {"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
        {"id": "p1-e1", "file": "photos/p1-e1.png", "origin": "edit", "source": "p1"},
        {"id": "p2", "file": "photos/p2.jpg", "origin": "upload"},
    ],
    "sections": [
        {"id": "s1", "kind": "hero", "headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러", "photo": "p1-e1"},
        {"id": "s2", "kind": "points", "headline": "이래서 좋아요", "body": "넉넉한 용량: 500ml로 하루 커피 두 잔"},
        {"id": "s3", "kind": "notice", "headline": "관리 방법", "body": "중성세제로 세척해 주세요."},
        {"id": "s4", "kind": "gallery", "variant": "grid-2", "headline": "더 많은 모습", "body": "", "photos": ["p2"]},
    ],
}


def calls_of(message):
    out = []
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = {"_raw": call["function"].get("arguments")}
        out.append((call["function"]["name"], args))
    return out


def has(calls, name, **expected):
    return any(n == name and all(a.get(k) == v for k, v in expected.items()) for n, a in calls)


TOOL_CASES = [
    ("헤드라인을 더 짧고 강하게 바꿔줘",
     lambda c: any(n == "update_text" and a.get("section_id") == "s1" and a.get("field") == "headline"
                   and 0 < len(a.get("text", "")) < len("하루 종일 따뜻하게") for n, a in c)),
    ("대표 사진 배경을 밝은 주방으로 바꿔줘", lambda c: has(c, "edit_photo", section_id="s1")),
    ("스펙 표 하나 추가해줘", lambda c: has(c, "add_section", kind="spec_table")),
    ("관리 방법 섹션 지워", lambda c: has(c, "remove_section", section_id="s3")),
    ("관리방법을 히어로 바로 다음으로 옮겨줘", lambda c: has(c, "move_section", section_id="s3", after_section_id="s1")),
    ("무게도 적어줘", lambda c: has(c, "ask_user")),
    ("식기세척기 사용 가능하다고 써줘",
     lambda c: any(n in ("update_text", "add_section") and "식기세척기" in json.dumps(a, ensure_ascii=False) for n, a in c)),
    ("헤드라인 짧게 하고 사진은 대리석 테이블 위로",
     lambda c: has(c, "update_text", section_id="s1", field="headline") and has(c, "edit_photo", section_id="s1")),
    ("고마워", lambda c: c == []),
    ("본문을 좀 더 감성적으로", lambda c: any(n == "update_text" and a.get("field") == "body" for n, a in c)),
    ("대표 사진 원래 사진으로 되돌려", lambda c: has(c, "use_photo", section_id="s1", photo_id="p1")),
    ("히어로 글씨를 사진 위에 올려줘", lambda c: has(c, "set_variant", section_id="s1", variant="overlay")),
    ("갤러리를 3열로 바꿔줘", lambda c: has(c, "set_variant", section_id="s4", variant="grid-3")),
]

BASIS = "근거 텍스트: 스테인리스 텀블러\n이중 진공 단열, 500ml, 색상 3종\n사진에서 보이는 것: 무광 스테인리스 표면, 원터치 뚜껑, 실버"
JUDGE_CASES = [  # (text, extra user message added to the basis, should be flagged)
    ("하루 종일 따뜻하고 차갑게", "", True),
    ("식기세척기 사용 가능", "", True),
    ("12시간 보냉 유지", "", True),
    ("식기세척기 사용 가능", "식기세척기 써도 돼요", False),
    ("이중 진공 단열로 따뜻함이 오래가요", "", False),
    ("하루를 산뜻하게", "", False),
    ("당신의 책상 위 작은 여유", "", False),
    ("손에 착 감기는 그립감으로 어디서나 함께해요", "", False),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thinking", choices=["on", "off"], default="on")
    thinking = parser.parse_args().thinking == "on"
    system = prompts.chat_system(PAGE)

    tool_ok = 0
    for i, (text, check) in enumerate(TOOL_CASES, 1):
        started = time.perf_counter()
        message = llm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
            tools=prompts.TOOLS,
            thinking=thinking,
            max_tokens=1024,
        )
        calls = calls_of(message)
        ok = check(calls)
        tool_ok += ok
        print(
            f"tool #{i:02d} {'OK ' if ok else 'BAD'} {time.perf_counter() - started:5.2f}s {text} -> "
            f"{json.dumps(calls, ensure_ascii=False)[:160]} "
            f"{('| ' + message.get('content', '')[:60]) if not calls else ''}"
        )

    judge_ok = 0
    for text, said, should_flag in JUDGE_CASES:
        started = time.perf_counter()
        try:
            flagged = agent.judge(llm, text, BASIS + (f"\n{said}" if said else ""))
        except llm.LLMError as error:
            flagged = [f"ERROR {error}"]
        ok = bool(flagged) == should_flag
        judge_ok += ok
        print(f"judge {'OK ' if ok else 'BAD'} {time.perf_counter() - started:5.2f}s {text!r} -> {flagged}")

    tool_target = len(TOOL_CASES) - 2
    print(f"\ntools {tool_ok}/{len(TOOL_CASES)} (target ≥ {tool_target})  judge {judge_ok}/{len(JUDGE_CASES)} (target 8)")
    return 0 if tool_ok >= tool_target and judge_ok == len(JUDGE_CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
