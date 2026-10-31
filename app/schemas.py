from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, datetime
from .models import UserRole, BillingFrequency, SubscriptionStatus, InvoiceStatus, DiscountType


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    role: UserRole = UserRole.CUSTOMER

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        from .auth import validate_password
        ok, msg = validate_password(v)
        if not ok:
            raise ValueError(msg)
        return v


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ProductCreate(BaseModel):
    name: str
    product_type: str = "software"
    sales_price: float
    cost_price: float = 0.0
    notes: str = ""


class ProductVariantCreate(BaseModel):
    name: str
    price_extra: float = 0.0


class ProductOut(BaseModel):
    id: int
    name: str
    product_type: str
    sales_price: float
    cost_price: float
    notes: str
    is_active: bool
    variants: List[dict] = []

    class Config:
        from_attributes = True


class PlanCreate(BaseModel):
    name: str
    billing_frequency: BillingFrequency
    price: float
    min_quantity: int = 1
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    auto_close: bool = False
    allow_early_close: bool = True
    allow_pause: bool = True
    allow_renew: bool = True


class PlanOut(BaseModel):
    id: int
    name: str
    billing_frequency: BillingFrequency
    price: float
    min_quantity: int
    available_from: Optional[date]
    available_to: Optional[date]
    auto_close: bool
    allow_early_close: bool
    allow_pause: bool
    allow_renew: bool
    is_active: bool

    class Config:
        from_attributes = True


class SubscriptionLineCreate(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity: int = 1
    unit_price: float
    tax_rate: float = 0.0


class SubscriptionCreate(BaseModel):
    customer_id: int
    plan_id: int
    start_date: Optional[date] = None
    expiry_date: Optional[date] = None
    payment_terms: str = "Net 30"
    notes: str = ""
    lines: List[SubscriptionLineCreate] = []


class SubscriptionOut(BaseModel):
    id: int
    number: str
    customer_id: int
    plan_id: int
    status: SubscriptionStatus
    start_date: Optional[date]
    expiry_date: Optional[date]
    payment_terms: str
    notes: str
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceOut(BaseModel):
    id: int
    number: str
    subscription_id: int
    customer_id: int
    status: InvoiceStatus
    issue_date: date
    due_date: Optional[date]
    subtotal: float
    tax_amount: float
    discount_amount: float
    total: float

    class Config:
        from_attributes = True


class PaymentCreate(BaseModel):
    invoice_id: int
    amount: float
    method: str
    payment_date: Optional[date] = None
    note: str = ""


class DiscountCreate(BaseModel):
    name: str
    discount_type: DiscountType
    value: float
    min_purchase: float = 0.0
    min_quantity: int = 0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    max_uses: Optional[int] = None
    applies_to_product_id: Optional[int] = None
    applies_to_subscription: bool = False


class TaxRuleCreate(BaseModel):
    name: str
    rate: float
    is_default: bool = False


class DashboardStats(BaseModel):
    active_subscriptions: int
    total_revenue: float
    outstanding: float
    overdue_count: int
    payments_this_month: float
