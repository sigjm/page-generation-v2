"""Page state (page.json, spec §3) and the pure parts of the chat tools. No file or model access."""
import re
from collections.abc import Callable

KINDS = ("hero", "banner", "points", "split", "scene", "gallery", "spec_table", "notice")
PHOTO_KINDS = ("hero", "scene", "split")
VARIANTS = {"hero": ("below", "overlay"), "banner": ("light", "dark"), "points": ("cards", "numbered"),
            "split": ("image-left", "image-right"), "gallery": ("grid-2", "grid-3", "stack")}
SUPERLATIVES = ("최고", "최상", "최초", "1위", "완벽", "유일", "최저가", "넘버원", "no.1")
GALLERY_TITLE = "더 많은 모습"
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-zA-Z%℃°]+)?")


class ToolError(Exception):
    """A tool failure; its message goes back to the model as the tool result."""


def _no_check(text: str) -> None:
    pass


def corpus_of(page: dict, chat: list[dict]) -> str:
    """Grounding text (spec §5): product name, seller notes, every message the user typed."""
    product = page["product"]
    return "\n".join([product["name"], product["notes"], *(m["text"] for m in chat if m["role"] == "user")])


def numbers(text: str) -> set[tuple[str, str]]:
    return {(n.replace(",", "."), unit.lower()) for n, unit in _NUMBER.findall(text)}


def unsupported_numbers(text: str, corpus: str) -> list[str]:
    # ponytail: units are Latin letters, % and ℃ only; "3시간" compares the bare number and the claim check covers the rest
    have = numbers(corpus)
    bare = {n for n, _ in have}
    return sorted(n + unit for n, unit in numbers(text) if ((n, unit) not in have if unit else n not in bare))


def split_line(line: str) -> tuple[str, str]:
    """'제목: 설명' → ('제목', '설명'); splits at the first ':' so '10:30' values survive."""
    key, sep, value = line.partition(":")
    return (key.strip(), value.strip()) if sep else ("", line.strip())


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def is_sourced(value: str, corpus: str) -> bool:
    return _squash(value) in _squash(corpus)


def unsupported_spec_values(body: str, corpus: str) -> list[str]:
    values = (split_line(line)[1] for line in body.splitlines() if line.strip())
    return [v for v in values if not is_sourced(v, corpus)]


def unsupported_superlatives(text: str, corpus: str) -> list[str]:
    # ponytail: fixed word list; roundabout wording ("가장 좋은") is left to the claim judge
    low, have = text.casefold(), corpus.casefold()
    return [word for word in SUPERLATIVES if word in low and word not in have]


def check_text(text: str, corpus: str, *, spec: bool = False) -> None:
    bad = unsupported_numbers(text, corpus)
    if bad:
        raise ToolError(f"오류: 근거 없는 숫자 {', '.join(bad)}. 사용자에게 ask_user로 물어보세요.")
    bad = unsupported_superlatives(text, corpus)
    if bad:
        raise ToolError(f"오류: 근거 없는 최상급 표현 '{', '.join(bad)}'. 빼고 다시 쓰세요.")
    bad = unsupported_spec_values(text, corpus) if spec else []
    if bad:
        raise ToolError(f"오류: 근거 없는 스펙 값 {', '.join(bad)}. 사용자에게 ask_user로 물어보세요.")


def _index(page: dict, section_id: str) -> int:
    for i, section in enumerate(page["sections"]):
        if section["id"] == section_id:
            return i
    known = ", ".join(s["id"] for s in page["sections"])
    raise ToolError(f"오류: 없는 섹션 {section_id}. 있는 섹션: {known}")


def find_section(page: dict, section_id: str) -> dict:
    return page["sections"][_index(page, section_id)]


def find_photo(page: dict, photo_id: str) -> dict:
    for photo in page["photos"]:
        if photo["id"] == photo_id:
            return photo
    known = ", ".join(p["id"] for p in page["photos"])
    raise ToolError(f"오류: 없는 사진 {photo_id}. 있는 사진: {known}")


def next_section_id(page: dict) -> str:
    return f"s{max((int(s['id'][1:]) for s in page['sections']), default=0) + 1}"


def update_text(page: dict, section_id: str, field: str, text: str, corpus: str,
                verify: Callable[[str], None] = _no_check) -> str:
    section = find_section(page, section_id)
    if field not in ("headline", "body"):
        raise ToolError("오류: field는 headline 또는 body만 됩니다.")
    check_text(text, corpus, spec=section["kind"] == "spec_table" and field == "body")
    verify(text)
    section[field] = text
    return f"{section_id} {field} 변경"


def add_section(page: dict, kind: str, after_section_id: str, headline: str, body: str, corpus: str,
                verify: Callable[[str], None] = _no_check) -> str:
    if kind not in KINDS:
        raise ToolError(f"오류: kind는 {', '.join(KINDS)} 중 하나입니다.")
    position = _index(page, after_section_id) + 1 if after_section_id else 0
    check_text(headline, corpus)
    check_text(body, corpus, spec=kind == "spec_table")
    section = {"id": next_section_id(page), "kind": kind, "headline": headline, "body": body}
    if kind in VARIANTS:
        section["variant"] = VARIANTS[kind][0]
    uploads = [p["id"] for p in page["photos"] if p["origin"] == "upload"]
    if kind == "gallery":
        section["photos"] = uploads[1:]
        if not section["photos"]:
            raise ToolError("오류: 갤러리에 넣을 추가 사진이 없습니다.")
    elif kind in PHOTO_KINDS:
        section["photo"] = uploads[1] if kind == "split" and len(uploads) > 1 else uploads[0]
    verify(f"{headline}\n{body}")
    page["sections"].insert(position, section)
    return f"{section['id']}({kind}) 추가"


def set_variant(page: dict, section_id: str, variant: str) -> str:
    section = find_section(page, section_id)
    allowed = VARIANTS.get(section["kind"])
    if not allowed:
        raise ToolError(f"오류: {section['kind']} 섹션은 변형이 없습니다.")
    if variant not in allowed:
        raise ToolError(f"오류: {section['kind']} 변형은 {', '.join(allowed)} 중 하나입니다.")
    section["variant"] = variant
    return f"{section_id} 변형을 {variant}로 변경"


def remove_section(page: dict, section_id: str) -> str:
    page["sections"].pop(_index(page, section_id))
    return f"{section_id} 삭제"


def move_section(page: dict, section_id: str, after_section_id: str) -> str:
    if section_id == after_section_id:
        raise ToolError("오류: 섹션을 자기 자신 뒤로 옮길 수 없습니다.")
    index = _index(page, section_id)
    if after_section_id:
        _index(page, after_section_id)  # unknown anchor fails before anything moves
    section = page["sections"].pop(index)
    position = _index(page, after_section_id) + 1 if after_section_id else 0
    page["sections"].insert(position, section)
    return f"{section_id}를 {after_section_id + ' 다음' if after_section_id else '맨 앞'}으로 이동"


def _photo_section(page: dict, section_id: str) -> dict:
    section = find_section(page, section_id)
    if section["kind"] == "gallery":
        raise ToolError("오류: gallery 섹션은 사진을 하나씩 바꿀 수 없습니다. remove_section 후 add_section(gallery)를 쓰세요.")
    if section["kind"] not in PHOTO_KINDS:
        raise ToolError(f"오류: {section['kind']} 섹션에는 사진이 없습니다. 사진은 hero·scene·split에만 있습니다.")
    return section


def use_photo(page: dict, section_id: str, photo_id: str) -> str:
    section = _photo_section(page, section_id)
    find_photo(page, photo_id)
    section["photo"] = photo_id
    return f"{section_id} 사진을 {photo_id}로 교체"


def edit_source(page: dict, section_id: str) -> dict:
    """The uploaded original behind a photo section; edits always start from it (spec §4.2)."""
    photo = find_photo(page, _photo_section(page, section_id)["photo"])
    return find_photo(page, photo.get("source", photo["id"]))


def add_edit(page: dict, source_id: str, instruction: str) -> dict:
    n = 1 + sum(1 for p in page["photos"] if p.get("source") == source_id)
    photo = {"id": f"{source_id}-e{n}", "file": f"photos/{source_id}-e{n}.png", "origin": "edit",
             "source": source_id, "instruction": instruction}
    page["photos"].append(photo)
    return photo
