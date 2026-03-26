from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_user, require_role
from app.schemas.vehicle import MotorAttachmentRecord, MotorCatalogRecord, MotorCatalogUpsertRequest, MotorUpdateRequest
from app.services.motor_catalog import (
    create_motor,
    create_motor_attachment,
    delete_motor,
    delete_motor_attachment,
    get_motor_attachment_file,
    list_motor_attachments,
    list_motors,
    migrate_local_files_to_minio,
    update_motor,
    update_motor_attachment,
)

router = APIRouter(prefix="/motors", tags=["motors"])


@router.get("", response_model=list[MotorCatalogRecord])
def get_motors(_user: dict = Depends(get_current_user)) -> list[MotorCatalogRecord]:
    return list_motors()


@router.post("", response_model=MotorCatalogRecord, status_code=status.HTTP_201_CREATED)
def create_motor_record(
    payload: MotorCatalogUpsertRequest,
    _user: dict = Depends(require_role("admin", "editor")),
) -> MotorCatalogRecord:
    try:
        return create_motor(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{motor_id}", response_model=MotorCatalogRecord)
def update_motor_record(
    payload: MotorUpdateRequest,
    motor_id: int = Path(..., ge=1, description="ID del motor"),
    _user: dict = Depends(require_role("admin", "editor")),
) -> MotorCatalogRecord:
    try:
        return update_motor(motor_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{motor_id}", status_code=status.HTTP_200_OK)
def delete_motor_record(
    motor_id: int = Path(..., ge=1, description="ID del motor"),
    _user: dict = Depends(require_role("admin", "editor")),
) -> dict:
    try:
        vehicle_count = delete_motor(motor_id)
        return {"deleted": True, "vehicles_unlinked": vehicle_count}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{motor_id}/attachments", response_model=list[MotorAttachmentRecord])
def get_motor_attachments(
    motor_id: int = Path(..., ge=1, description="ID del motor"),
    _user: dict = Depends(get_current_user),
) -> list[MotorAttachmentRecord]:
    return list_motor_attachments(motor_id)


@router.post(
    "/{motor_id}/attachments",
    response_model=MotorAttachmentRecord,
    status_code=status.HTTP_201_CREATED,
)
def upload_motor_attachment(
    motor_id: int = Path(..., ge=1, description="ID del motor"),
    cpl: str = Form(..., min_length=1, description="CPL asociado al adjunto"),
    attachment: UploadFile = File(..., description="Imagen o PDF del motor"),
    _user: dict = Depends(require_role("admin", "editor")),
) -> MotorAttachmentRecord:
    try:
        return create_motor_attachment(
            motor_id,
            cpl=cpl,
            filename=attachment.filename or "",
            content_type=attachment.content_type,
            fileobj=attachment.file,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "no existe" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.put("/attachments/{attachment_id}", response_model=MotorAttachmentRecord)
def edit_motor_attachment(
    attachment_id: int = Path(..., ge=1, description="ID del adjunto"),
    cpl: str = Form(..., min_length=1, description="CPL asociado al adjunto"),
    attachment: UploadFile | None = File(default=None, description="Nuevo archivo opcional"),
    _user: dict = Depends(require_role("admin", "editor")),
) -> MotorAttachmentRecord:
    try:
        return update_motor_attachment(
            attachment_id,
            cpl=cpl,
            filename=attachment.filename if attachment else None,
            content_type=attachment.content_type if attachment else None,
            fileobj=attachment.file if attachment else None,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "no existe" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_motor_attachment(
    attachment_id: int = Path(..., ge=1, description="ID del adjunto"),
    _user: dict = Depends(require_role("admin", "editor")),
) -> None:
    try:
        delete_motor_attachment(attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/attachments/{attachment_id}/download")
def download_motor_attachment(
    attachment_id: int = Path(..., ge=1, description="ID del adjunto"),
    _user: dict = Depends(get_current_user),
) -> StreamingResponse:
    try:
        attachment, file_stream = get_motor_attachment_file(attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    encoded_filename = quote(attachment.original_filename)
    return StreamingResponse(
        content=file_stream,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
        },
    )


@router.post("/migrate-attachments", status_code=status.HTTP_200_OK)
def migrate_attachments_to_minio(
    _user: dict = Depends(require_role("admin")),
) -> dict:
    """Migra archivos locales existentes a MinIO (operacion unica)."""
    result = migrate_local_files_to_minio()
    return result
