from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email", "password")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Поле обязательно")
        return value


class UserProfile(BaseModel):
    id: int | str
    email: str
    first_name: str | None = None
    status: str | None = None
    user_permissions: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    user: UserProfile
    csrf_token: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class SessionData(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: UserProfile
    csrf_token: str
    created_at: datetime
    absolute_expires_at: datetime


class DomofonConnectRequest(BaseModel):
    service_login: str = Field(min_length=1, max_length=255)

    @field_validator("service_login")
    @classmethod
    def strip_login(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Логин обязателен")
        return value


class DomofonCreateRequest(BaseModel):
    locid: int = Field(strict=True)
    field_flat: int = Field(strict=True)
    field_podezd: int = Field(strict=True)
    client_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("client_name")
    @classmethod
    def strip_client_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ФИО обязательно")
        return value

    @field_validator("phone")
    @classmethod
    def strip_optional_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class DomofonAddress(BaseModel):
    locid: int
    loctext: str


class DomofonOperationResponse(BaseModel):
    ok: bool
    reason: str


class TechPortalAddress(BaseModel):
    externalAddress: str | None = None
    house: str | int | None = None
    apartment: str | int | None = None


class TechPortalHistoryChange(BaseModel):
    key: str | None = None
    value: Any = None


class TechPortalHistoryItem(BaseModel):
    changes: list[TechPortalHistoryChange] = Field(default_factory=list)
    createdAt: datetime | None = None
    techportalUser: int | str | None = None


class TechPortalTicket(BaseModel):
    id: int
    masters: list[int | str] = Field(default_factory=list)
    scheduledDate: datetime | None = None
    description: str | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    clientName: str | None = None
    clientPhone: str | None = None
    phones: list[str] = Field(default_factory=list)
    address: TechPortalAddress | None = None
    history: list[TechPortalHistoryItem] = Field(default_factory=list)


class TechPortalUser(BaseModel):
    id: int | str
    name: str | None = None
    firstName: str | None = None
    lastName: str | None = None


class TicketComment(BaseModel):
    created_at: datetime | None = None
    author: str
    text: str


class TicketResponse(BaseModel):
    id: int
    address: str
    client_phone: str
    client_name: str
    description: str
    scheduled_at: datetime | None = None
    kind: Literal["connection", "repair"]
    completed: bool
    tags: dict[str, Any]
    comments: list[TicketComment]


class TicketCompletionRequest(BaseModel):
    completed: bool
    day: Literal["today", "tomorrow"]
