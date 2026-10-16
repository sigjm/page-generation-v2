import copy

import pytest

PAGE = {
    "id": "20261016-153012-ab12",
    "version": 1,
    "product": {"name": "스테인리스 텀블러", "notes": "이중 진공 단열, 500ml, 색상 3종"},
    "analysis": {
        "category": "주방용품",
        "visible_features": ["원터치 뚜껑"],
        "colors": ["실버"],
        "facts": [{"key": "용량", "value": "500ml"}],
        "questions": [],
    },
    "photos": [
        {"id": "p1", "file": "photos/p1.jpg", "origin": "upload"},
        {"id": "p1-e1", "file": "photos/p1-e1.png", "origin": "edit", "source": "p1", "instruction": "대리석"},
    ],
    "sections": [
        {"id": "s1", "kind": "hero", "headline": "하루 종일 따뜻하게", "body": "이중 진공 단열 텀블러", "photo": "p1-e1"},
        {"id": "s2", "kind": "points", "headline": "이래서 좋아요", "body": "넉넉한 용량: 500ml"},
        {"id": "s3", "kind": "notice", "headline": "관리 방법", "body": ""},
    ],
}


@pytest.fixture
def page():
    return copy.deepcopy(PAGE)
