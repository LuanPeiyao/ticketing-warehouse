"""HTTP 输入与输出契约。响应模型明确排除密码哈希、令牌摘要和他人的资料。"""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


Identifier = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]
Money = Annotated[str, Field(pattern=r'^(0|[1-9]\d{0,11})(\.\d{1,2})?$')]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Registration(Input):
    email: EmailStr = Field(max_length=254)
    display_name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=10, max_length=128)

    @field_validator('email', mode='before')
    @classmethod
    def trim_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator('display_name')
    @classmethod
    def name_nonblank(cls, value):
        if not value.strip():
            raise ValueError('显示名称不能为空')
        return value.strip()


class Login(Input):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator('email', mode='before')
    @classmethod
    def trim_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class User(BaseModel):
    user_id: int
    email: str
    display_name: str
    role: str
    status: str


class Token(BaseModel):
    access_token: str
    token_type: Literal['bearer']
    expires_in: int
    expires_at: datetime


class Reservation(Input):
    session_id: Identifier
    inventory_ids: list[Identifier] = Field(min_length=1, max_length=50)
    request_id: UUID

    @field_validator('inventory_ids')
    @classmethod
    def distinct_seats(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('座位不能重复')
        return value


class PaymentInput(Input):
    amount: Money
    request_id: UUID
    outcome: Literal['SUCCEEDED', 'FAILED'] = 'SUCCEEDED'


class RefundInput(Input):
    reason: str = Field(min_length=1, max_length=200)
    request_id: UUID
    outcome: Literal['SUCCEEDED', 'FAILED'] = 'SUCCEEDED'

    @field_validator('reason')
    @classmethod
    def reason_nonblank(cls, value):
        if not value.strip():
            raise ValueError('退款原因不能为空')
        return value.strip()


class OrderSummary(BaseModel):
    activity_title: str
    venue_name: str
    session_start_time: datetime
    order_id: int
    order_no: str
    user_id: int
    session_id: int
    total_amount: Decimal
    status: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None
    refunded_at: datetime | None


class Item(BaseModel):
    seat_code: str
    item_id: int
    session_seat_id: int
    unit_price: Decimal


class History(BaseModel):
    from_status: str | None
    to_status: str
    reason: str
    changed_at: datetime


class Payment(BaseModel):
    payment_id: int
    payment_no: str
    order_id: int
    request_id: UUID
    amount: Decimal
    provider: str
    status: str
    created_at: datetime


class Ticket(BaseModel):
    ticket_id: int
    ticket_no: UUID
    item_id: int
    order_id: int
    session_seat_id: int
    status: str
    issued_at: datetime
    refunded_at: datetime | None


class Refund(BaseModel):
    refund_id: int
    refund_no: str
    payment_id: int
    order_id: int
    request_id: UUID
    amount: Decimal
    reason: str
    status: str
    created_at: datetime


class Order(OrderSummary):
    request_id: UUID
    items: list[Item]
    history: list[History]
    payments: list[Payment]
    tickets: list[Ticket]
    refunds: list[Refund]


class PaymentResult(BaseModel):
    payment: Payment | None
    order: Order


class RefundResult(BaseModel):
    refund: Refund
    order: Order


class Activity(BaseModel):
    activity_id: int
    title: str
    description: str


class Session(BaseModel):
    session_id: int
    activity_id: int
    title: str
    venue_id: int
    venue_name: str
    start_time: datetime
    end_time: datetime


class Inventory(BaseModel):
    session_seat_id: int
    seat_code: str
    row_label: str
    seat_number: int
    price: Decimal
    status: str
    locked_until: datetime | None


class ValidationIssue(BaseModel):
    location: list[str | int]
    type: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo
