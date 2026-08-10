from __future__ import annotations

import typer


def build_attachment_payload(
    material_ids: list[int] | None,
    *,
    field_name: str,
    material_option: str,
    clear_attachments: bool = False,
    preserve_when_omitted: bool = True,
) -> dict[str, object]:
    """Build an attachment update without collapsing omitted into clear."""
    if material_ids is not None:
        if any(material_id < 1 for material_id in material_ids):
            raise typer.BadParameter(
                f"{material_option} 必须是正整数。",
                param_hint=material_option,
            )
        if len(material_ids) != len(set(material_ids)):
            raise typer.BadParameter(
                f"{material_option} 不能重复。",
                param_hint=material_option,
            )
    if clear_attachments and material_ids is not None:
        raise typer.BadParameter(
            f"--clear-attachments 不能和 {material_option} 同时使用。",
            param_hint="--clear-attachments",
        )
    if clear_attachments:
        return {field_name: []}
    if material_ids is not None:
        return {field_name: list(material_ids)}
    if preserve_when_omitted:
        return {}
    return {field_name: []}
