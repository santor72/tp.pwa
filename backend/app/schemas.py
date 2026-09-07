import re
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.roles import UserRole, role_for_status


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

    @computed_field
    @property
    def role(self) -> UserRole:
        return role_for_status(self.status)


class Capabilities(BaseModel):
    payments: bool = False
    messenger_settings: bool = False
    all_tickets: bool = False
    payment_admin: bool = False


class SessionResponse(BaseModel):
    user: UserProfile
    csrf_token: str
    capabilities: Capabilities = Field(default_factory=Capabilities)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class SessionData(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: UserProfile
    internal_user_id: UUID | None = None
    upstream_cookies: dict[str, str] = Field(default_factory=dict)
    csrf_token: str
    created_at: datetime
    absolute_expires_at: datetime


class AdminPaymentListQuery(BaseModel):
    date_from: datetime | None = None
    date_to: datetime | None = None
    phone: str | None = Field(default=None, max_length=64)
    employee: str | None = Field(default=None, max_length=255)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class AdminPaymentEvent(BaseModel):
    id: UUID
    event_type: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class AdminPaymentItem(BaseModel):
    id: UUID
    paid_at: datetime
    actual_amount: Decimal
    currency: str
    product_title: str
    status: Literal["paid"]
    phone: str
    email: str | None = None
    employee_display_name: str | None = None
    employee_external_id: str | None = None
    bitrix_lead_id: int | None = None
    bitrix_contact_id: int | None = None
    bitrix_invoice_id: int | None = None
    bitrix_payment_id: int | None = None
    bitrix_payment_account_number: str | None = None
    bitrix_pay_system_name: str | None = None


class AdminPaymentDetail(AdminPaymentItem):
    catalog_amount: Decimal
    address_text: str | None = None
    apartment: str | None = None
    created_at: datetime
    updated_at: datetime
    events: list[AdminPaymentEvent] = Field(default_factory=list)


class AdminPaymentListResponse(BaseModel):
    items: list[AdminPaymentItem]
    page: int
    page_size: int
    total: int


class PaymentAddress(BaseModel):
    locid: int = Field(gt=0)
    loctext: str = Field(min_length=1, max_length=1000)

    @field_validator("loctext")
    @classmethod
    def strip_address(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Адрес обязателен")
        return value


class PaymentProduct(BaseModel):
    product_id: int
    title: str
    default_amount: Decimal
    currency: str = "RUB"
    price_override_allowed: bool = True


class PaymentCreateRequest(BaseModel):
    idempotency_key: UUID
    address: PaymentAddress | None = None
    apartment: str | None = Field(default=None, max_length=64)
    product_id: int = Field(gt=0)
    first_name: str = Field(min_length=1, max_length=255)
    second_name: str | None = Field(default=None, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    amount: Decimal

    @field_validator("first_name", "last_name")
    @classmethod
    def strip_required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Поле ФИО обязательно")
        return value

    @field_validator("second_name", "apartment")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        value = value.strip().lower() if value else None
        if not value:
            return None
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Некорректный e-mail")
        return value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        try:
            number = phonenumbers.parse(value.strip(), "RU")
        except phonenumbers.NumberParseException as exc:
            raise ValueError("Некорректный номер телефона") from exc
        if not phonenumbers.is_valid_number(number):
            raise ValueError("Некорректный номер телефона")
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: Any) -> Decimal:
        if not isinstance(value, str):
            raise ValueError("Сумма должна передаваться строкой")
        try:
            amount = Decimal(value.replace(",", ".")).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Некорректная сумма") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        return amount

    @model_validator(mode="after")
    def validate_address_apartment(self) -> "PaymentCreateRequest":
        if self.address is not None and not self.apartment:
            raise ValueError("Для выбранного адреса укажите квартиру или офис")
        if self.address is None and self.apartment is not None:
            raise ValueError("Квартира не передаётся без адреса")
        return self


class PaymentCandidate(BaseModel):
    entity_type: Literal["contact", "lead"]
    entity_id: int
    display_name: str
    phone_hint: str | None = None


class PaymentClientSelectionRequest(BaseModel):
    entity_type: Literal["contact", "lead"]
    entity_id: int = Field(gt=0)


PaymentStatus = Literal[
    "draft", "resolving_client", "client_selection_required", "client_resolved",
    "invoice_created", "product_added", "payment_created", "link_created",
    "send_queued", "sent", "paid", "send_failed", "failed", "expired", "canceled",
]


class PaymentTransactionResponse(BaseModel):
    id: UUID
    status: PaymentStatus
    current_step: str
    send_status: str | None = None
    product_title: str
    catalog_amount: Decimal
    actual_amount: Decimal
    currency: str
    payment_url: str | None = None
    payment_short_url: str | None = None
    payment_qr: str | None = None
    candidates: list[PaymentCandidate] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class PaymentRecentResponse(BaseModel):
    id: UUID
    status: PaymentStatus
    product_title: str
    actual_amount: Decimal
    currency: str
    created_at: datetime


class PaymentAcceptedResponse(BaseModel):
    id: UUID
    status: PaymentStatus


class BitrixPaymentWebhook(BaseModel):
    event: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


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
    client_phones: list[str] = Field(default_factory=list)
    client_name: str
    description: str
    scheduled_at: datetime | None = None
    kind: Literal["connection", "repair"]
    completed: bool
    tags: dict[str, Any]
    comments: list[TicketComment]
    assigned_masters: list[str] = Field(default_factory=list)
    can_change_completion: bool = True


class TicketCompletionRequest(BaseModel):
    completed: bool
    day: Literal["today", "tomorrow"]
    comment: str | None = Field(default=None, max_length=4000)

    @field_validator("comment")
    @classmethod
    def strip_comment(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class DialRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=32)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            raise ValueError("Укажите корректный номер телефона")
        return f"+{digits}"


class MessengerLinkResponse(BaseModel):
    provider: Literal["telegram"]
    linked_at: datetime
    username: str | None = None
    display_name: str | None = None


class MessengerLinkCreateResponse(BaseModel):
    provider: Literal["telegram"]
    deep_link: str
    expires_at: datetime
