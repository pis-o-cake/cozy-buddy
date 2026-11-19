"""LLM 툴 정의를 만드는 공통 도구."""

from typing import Any

from app.domain.agent.home_view import HomeView


def catalog(view: HomeView, domains: frozenset[str] | None) -> tuple[list[str], str]:
    """툴 설명에 넣을 엔티티 ID 목록과 "id=방 이름" 카탈로그 문자열."""
    ids: list[str] = []
    lines: list[str] = []
    for entity in view.model.entities:
        if domains is not None and entity.domain not in domains:
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
