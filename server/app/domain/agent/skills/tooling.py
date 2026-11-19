"""LLM 툴 정의를 만드는 공통 도구."""

from collections.abc import Callable
from typing import Any

from app.domain.agent.home_view import HomeView


def catalog(
    view: HomeView,
    domains: frozenset[str] | None,
    allow: Callable[[str], bool] | None = None,
) -> tuple[list[str], str]:
    """툴 설명에 넣을 엔티티 ID 목록과 "id=방 이름" 카탈로그 문자열.

    Args:
        view: 요청이 보는 집.
        domains: 포함할 도메인. None 이면 전체.
        allow: entity_id 를 받아 포함 여부를 정하는 추가 조건.
    """
    ids: list[str] = []
    lines: list[str] = []
    for entity in view.model.entities:
        if domains is not None and entity.domain not in domains:
            continue
        if allow is not None and not allow(entity.entity_id):
            continue
        ids.append(entity.entity_id)
        area = view.area_name(entity.area_id)
        label = f"{area} {entity.name}".strip()
        lines.append(f"{entity.entity_id}={label}")
    return ids, "; ".join(lines)


def function_tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """OpenAI chat `tools` 형식의 함수 정의 하나."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }
