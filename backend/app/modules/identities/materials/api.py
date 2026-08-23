from __future__ import annotations

from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session
from app.models import (
    IdentityMaterial,
    IdentityMaterialType,
)
from app.services.file_storage import (
    delete_file,
)
from .schemas import IdentityMaterialRead, MaterialDeletionImpactRead
from .serializer import serialize_material
from .service import (
    MaterialMutationError,
    delete_identity_material_record,
    prepare_material_deletion_snapshot,
    set_primary_material_record,
    upload_identity_material_record,
)
from .support import build_material_download_name


router = APIRouter(prefix="/api", tags=["materials"])


@router.get("/materials", response_model=list[IdentityMaterialRead])
async def list_materials(
    identity_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> list[IdentityMaterialRead]:
    if identity_id is not None:
        await _get_identity_or_404(session, identity_id)
    materials = list(
        (
            await session.scalars(
                select(IdentityMaterial)
                .options(selectinload(IdentityMaterial.default_for_identities))
                .order_by(
                    IdentityMaterial.created_at.desc(), IdentityMaterial.id.desc()
                ),
            )
        ).unique(),
    )
    return [
        serialize_material(
            material,
            current_primary_material_id=(
                material.id
                if identity_id is not None
                and any(
                    identity.id == identity_id
                    for identity in material.default_for_identities
                )
                else None
            ),
            default_for_identity_ids=[
                identity.id for identity in material.default_for_identities
            ],
        )
        for material in materials
    ]


@router.post(
    "/materials",
    response_model=IdentityMaterialRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_global_material(
    file: UploadFile = File(...),
    material_type: str = Form(default=IdentityMaterialType.OTHER.value),
    display_name: str | None = Form(default=None),
    identity_id: int | None = Form(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    return await _upload_material(
        session,
        identity_id=identity_id,
        file=file,
        material_type=material_type,
        display_name=display_name,
    )


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
    return await _upload_material(
        session,
        identity_id=identity_id,
        file=file,
        material_type=material_type,
        display_name=display_name,
    )


async def _upload_material(
    session: AsyncSession,
    *,
    identity_id: int | None,
    file: UploadFile,
    material_type: str,
    display_name: str | None,
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
    return serialize_material(
        material,
        primary_material_id,
        default_for_identity_ids=(
            [identity_id]
            if identity_id is not None and primary_material_id == material.id
            else []
        ),
    )


@router.post(
    "/materials/{material_id}/set-primary", response_model=IdentityMaterialRead
)
async def set_primary_material(
    material_id: int,
    identity_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    try:
        material, primary_material_id = await set_primary_material_record(
            session,
            material_id,
            identity_id=identity_id,
            event_name="identity_material.primary_set",
            actor="ui",
        )
    except MaterialMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    target_identity_id = (
        identity_id if identity_id is not None else material.identity_id
    )
    return serialize_material(
        material,
        primary_material_id,
        default_for_identity_ids=_default_identity_ids(material, target_identity_id),
    )


@router.post(
    "/identities/{identity_id}/materials/{material_id}/set-primary",
    response_model=IdentityMaterialRead,
)
async def set_identity_primary_material(
    identity_id: int,
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    try:
        material, primary_material_id = await set_primary_material_record(
            session,
            material_id,
            identity_id=identity_id,
            event_name="identity_material.primary_set",
            actor="ui",
        )
    except MaterialMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return serialize_material(
        material,
        primary_material_id,
        default_for_identity_ids=_default_identity_ids(material, identity_id),
    )


@router.get(
    "/materials/{material_id}/deletion-impact",
    response_model=MaterialDeletionImpactRead,
)
async def get_material_deletion_impact(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MaterialDeletionImpactRead:
    try:
        snapshot = await prepare_material_deletion_snapshot(session, material_id)
    except MaterialMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return MaterialDeletionImpactRead.model_validate(snapshot)


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: int,
    deletion_fingerprint: str | None = Query(default=None, min_length=16, max_length=128),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    try:
        expected_fingerprint = deletion_fingerprint
        if expected_fingerprint is None:
            preview = await prepare_material_deletion_snapshot(session, material_id)
            expected_fingerprint = str(preview["deletion_fingerprint"])
        result = await delete_identity_material_record(
            session,
            material_id,
            event_name="identity_material.deleted",
            actor="ui",
            expected_fingerprint=expected_fingerprint,
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
        select(IdentityMaterial).where(IdentityMaterial.id == material_id),
    )
    if not material:
        raise HTTPException(status_code=404, detail="未找到材料")
    return material


async def _get_identity_or_404(session: AsyncSession, identity_id: int) -> None:
    from app.models import IdentityProfile

    if await session.get(IdentityProfile, identity_id) is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")


def _default_identity_ids(
    material: IdentityMaterial,
    target_identity_id: int | None,
) -> list[int]:
    identity_ids = {identity.id for identity in material.default_for_identities}
    if target_identity_id is not None:
        identity_ids.add(target_identity_id)
    return sorted(identity_ids)
