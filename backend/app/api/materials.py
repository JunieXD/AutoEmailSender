from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.identity_serializers import serialize_material
from app.core.database import get_async_session
from app.models import (
    IdentityMaterial,
    IdentityMaterialType,
)
from app.schemas.identity import IdentityMaterialRead
from app.services.file_storage import (
    delete_file,
)
from app.services.material_mutations import (
    MaterialMutationError,
    delete_identity_material_record,
    set_primary_material_record,
    upload_identity_material_record,
)
from app.services.materials import build_material_download_name


router = APIRouter(prefix="/api", tags=["materials"])

@router.post(
    "/identities/{identity_id}/materials",
    response_model=IdentityMaterialRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_identity_material(
    identity_id: int,
    file: UploadFile = File(...),
    material_type: str = Form(default=IdentityMaterialType.OTHER.value),
    display_name: str | None = Form(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    try:
        material, primary_material_id = await upload_identity_material_record(
            session,
            identity_id,
            file,
            material_type,
            display_name,
            event_name="identity_material.uploaded",
            actor="ui",
        )
    except MaterialMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    await session.refresh(material)
    return serialize_material(material, primary_material_id)


@router.post("/materials/{material_id}/set-primary", response_model=IdentityMaterialRead)
async def set_primary_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    try:
        material, primary_material_id = await set_primary_material_record(
            session,
            material_id,
            event_name="identity_material.primary_set",
            actor="ui",
        )
    except MaterialMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return serialize_material(material, primary_material_id)


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    try:
        result = await delete_identity_material_record(
            session,
            material_id,
            event_name="identity_material.deleted",
            actor="ui",
        )
    except MaterialMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    delete_file(result.file_path)


@router.get("/materials/{material_id}/open")
async def open_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
):
    material = await _get_material(session, material_id)
    file_path = Path(material.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="材料文件不存在")

    response = FileResponse(file_path, media_type=material.mime_type)
    response.headers["Content-Disposition"] = "inline"
    return response


@router.get("/materials/{material_id}/download")
async def download_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
):
    material = await _get_material(session, material_id)
    file_path = Path(material.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="材料文件不存在")

    return FileResponse(
        file_path,
        media_type=material.mime_type,
        filename=build_material_download_name(material),
    )


async def _get_material(session: AsyncSession, material_id: int) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial)
        .options(selectinload(IdentityMaterial.identity))
        .where(IdentityMaterial.id == material_id),
    )
    if not material:
        raise HTTPException(status_code=404, detail="未找到材料")
    return material
