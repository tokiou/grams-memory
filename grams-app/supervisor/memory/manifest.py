"""Helpers for resolving memory hierarchy IDs from the live manifest."""

from typing import Any


def categories(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for project in (manifest or {}).get("projects", []):
        if not isinstance(project, dict):
            continue
        for key in project.get("keys", []):
            if not isinstance(key, dict):
                continue
            for category in key.get("categories", []):
                if isinstance(category, dict):
                    result.append({
                        **category,
                        "project_id": project.get("id"),
                        "project_name": project.get("name"),
                        "key_id": key.get("id"),
                        "key_name": key.get("name"),
                    })
    return result


def resolve_category(manifest: dict[str, Any] | None, arguments: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any] | None:
    available = categories(manifest)
    requested_id = arguments.get("category_id")
    if requested_id:
        return next((item for item in available if item.get("id") == requested_id), None)
    requested_name = str(arguments.get("category_name") or arguments.get("category") or "").strip().lower()
    if requested_name:
        match = next((item for item in available if str(item.get("name", "")).lower() == requested_name), None)
        if match:
            return match
    requested_type = str(arguments.get("type") or "").strip().lower()
    if requested_type:
        match = next((item for item in available if str(item.get("name", "")).lower() in {requested_type, f"{requested_type}s"}), None)
        if match:
            return match
    if fallback_id:
        return next((item for item in available if item.get("id") == fallback_id), None)
    return available[0] if available else None


def validate_scope(manifest: dict[str, Any] | None, arguments: dict[str, Any]) -> None:
    available = categories(manifest)
    category_id = arguments.get("category_id")
    matches = [item for item in available if not category_id or item.get("id") == category_id]
    if category_id and not matches:
        raise ValueError(f"category_id is not present in the current memory manifest: {category_id}")
    for argument, field, label in (("key_id", "key_id", "key"), ("project_id", "project_id", "project")):
        value = arguments.get(argument)
        if value and not any(item.get(field) == value for item in matches):
            raise ValueError(f"{label}_id is not consistent with the current memory scope: {value}")


def scope_for_category(manifest: dict[str, Any] | None, category_id: str | None) -> dict[str, str]:
    match = next((item for item in categories(manifest) if item.get("id") == category_id), None)
    if not match:
        return {}
    return {key: str(match[key]) for key in ("project_id", "key_id") if match.get(key)}
