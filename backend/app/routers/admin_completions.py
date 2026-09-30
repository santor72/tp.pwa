from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.dependencies import require_admin

router = APIRouter(prefix='/api/admin/ticket-completions', tags=['admin-completions'])

class Attempt(BaseModel):
    id: UUID
    system: str
    action: str
    success: bool
    http_status: int | None
    response_body: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime

class Item(BaseModel):
    id: UUID
    ticket_id: int
    ticket_kind: str
    subscriber_login: str | None
    subscriber_address: str | None
    technician_name: str
    technician_external_id: str
    completion_status: str
    gis_status: str
    created_at: datetime
    techportal_completed_at: datetime | None
    updated_at: datetime

class Page(BaseModel):
    items: list[Item]
    total: int
    page: int
    page_size: int

class Detail(Item):
    attempts: list[Attempt] = Field(default_factory=list)
    gis_report_url: str | None = None

@router.get('', response_model=Page)
async def list_completions(request: Request, date_from: datetime | None = None, date_to: datetime | None = None,
    ticket_id: int | None = None, login: str | None = Query(default=None, max_length=255),
    employee: str | None = Query(default=None, max_length=255), completion_status: str | None = None,
    gis_status: str | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100),
    admin=Depends(require_admin)):
    rows, total = await request.app.state.completion_repository.admin_list(date_from=date_from, date_to=date_to,
        ticket_id=ticket_id, login=login, employee=employee, completion_status=completion_status,
        gis_status=gis_status, page=page, page_size=page_size)
    return Page(items=[Item.model_validate(row, from_attributes=True) for row in rows], total=total, page=page, page_size=page_size)

@router.get('/{operation_id}', response_model=Detail)
async def completion_detail(operation_id: UUID, request: Request, admin=Depends(require_admin)):
    operation, attempts = await request.app.state.completion_repository.admin_get(operation_id)
    if operation is None: raise HTTPException(404, 'Операция закрытия не найдена')
    gis_report_url = None
    map_id = (operation.feature_snapshot or {}).get('map_id')
    if operation.gis_status == 'delivered' and map_id and operation.feature_id and operation.external_report_id:
        gis_address = request.app.state.settings.gis_base_url.rstrip('/')
        if gis_address:
            query = urlencode({'map': str(map_id), 'feature': str(operation.feature_id),
                               'report': str(operation.id)})
            gis_report_url = f'{gis_address}/?{query}'
    return Detail(**Item.model_validate(operation, from_attributes=True).model_dump(),
        attempts=[Attempt.model_validate(row, from_attributes=True) for row in attempts],
        gis_report_url=gis_report_url)
