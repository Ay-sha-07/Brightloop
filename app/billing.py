"""Billing helpers: tax, discounts, status transitions, invoice totals."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from .models import (
    Discount, DiscountType, TaxRule, Invoice, InvoiceStatus,
    Subscription, SubscriptionStatus, Payment,
)


def money(value) -> float:
    """Round to 2 decimal places using banker's-safe half-up."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --- Status state machines ---

ALLOWED_SUB_TRANSITIONS = {
    SubscriptionStatus.DRAFT: {SubscriptionStatus.QUOTATION, SubscriptionStatus.CLOSED},
    SubscriptionStatus.QUOTATION: {SubscriptionStatus.CONFIRMED, SubscriptionStatus.CLOSED},
    SubscriptionStatus.CONFIRMED: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CLOSED},
    SubscriptionStatus.ACTIVE: {SubscriptionStatus.PAUSED, SubscriptionStatus.CLOSED},
    SubscriptionStatus.PAUSED: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CLOSED},
    SubscriptionStatus.CLOSED: set(),  # terminal
}

ALLOWED_INV_TRANSITIONS = {
    InvoiceStatus.DRAFT: {InvoiceStatus.CONFIRMED, InvoiceStatus.CANCELLED},
    InvoiceStatus.CONFIRMED: {InvoiceStatus.PAID, InvoiceStatus.CANCELLED},
    InvoiceStatus.PAID: set(),
    InvoiceStatus.CANCELLED: set(),
}


def can_transition_sub(current: SubscriptionStatus, new: SubscriptionStatus) -> bool:
    return new in ALLOWED_SUB_TRANSITIONS.get(current, set())


def can_transition_inv(current: InvoiceStatus, new: InvoiceStatus) -> bool:
    return new in ALLOWED_INV_TRANSITIONS.get(current, set())


# --- Tax ---

def get_default_tax_rate(db: Session) -> float:
    rule = db.query(TaxRule).filter(TaxRule.is_default == True, TaxRule.is_active == True).first()
    return float(rule.rate) if rule else 0.0


# --- Discounts ---

def find_applicable_discounts(
    db: Session,
    *,
    subtotal: float,
    quantity: int = 1,
    product_id: Optional[int] = None,
    for_subscription: bool = True,
) -> List[Discount]:
    today = date.today()
    q = db.query(Discount).filter(Discount.is_active == True)
    candidates = q.all()
    applicable = []
    for d in candidates:
        if d.start_date and today < d.start_date:
            continue
        if d.end_date and today > d.end_date:
            continue
        if d.max_uses is not None and d.used_count >= d.max_uses:
            continue
        if d.min_purchase and subtotal < d.min_purchase:
            continue
        if d.min_quantity and quantity < d.min_quantity:
            continue
        if d.applies_to_product_id and product_id and d.applies_to_product_id != product_id:
            continue
        if d.applies_to_subscription and not for_subscription:
            continue
        if not d.applies_to_subscription and d.applies_to_product_id is None and product_id:
            # product-scoped discount without matching product
            continue
        applicable.append(d)
    return applicable


def compute_discount_amount(discounts: List[Discount], subtotal: float) -> Tuple[float, List[Discount]]:
    """Apply the single best discount (highest savings). Returns (amount, [used])."""
    if not discounts or subtotal <= 0:
        return 0.0, []
    best_amt = 0.0
    best_d = None
    for d in discounts:
        if d.discount_type == DiscountType.PERCENTAGE:
            amt = subtotal * (float(d.value) / 100.0)
        else:
            amt = float(d.value)
        amt = min(amt, subtotal)  # never exceed subtotal
        if amt > best_amt:
            best_amt = amt
            best_d = d
    if best_d is None:
        return 0.0, []
    return money(best_amt), [best_d]


def mark_discounts_used(db: Session, discounts: List[Discount]) -> None:
    for d in discounts:
        d.used_count = (d.used_count or 0) + 1
    db.flush()


# --- Invoice totals ---

def compute_invoice_totals(
    db: Session,
    *,
    line_amounts: List[float],
    total_quantity: int = 1,
    product_ids: Optional[List[int]] = None,
) -> dict:
    subtotal = money(sum(line_amounts))
    tax_rate = get_default_tax_rate(db)
    # Prefer product-scoped discounts if a single product dominates; else subscription-level
    discounts = find_applicable_discounts(
        db, subtotal=subtotal, quantity=total_quantity, for_subscription=True
    )
    discount_amount, used = compute_discount_amount(discounts, subtotal)
    taxable = max(subtotal - discount_amount, 0.0)
    tax_amount = money(taxable * (tax_rate / 100.0))
    total = money(taxable + tax_amount)
    return {
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total": total,
        "discounts_used": used,
    }


def amount_paid(db: Session, invoice_id: int) -> float:
    from sqlalchemy import func
    total = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
        Payment.invoice_id == invoice_id
    ).scalar()
    return money(total or 0.0)


def amount_due(db: Session, inv: Invoice) -> float:
    if inv.status in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED):
        return 0.0
    return money(max(float(inv.total) - amount_paid(db, inv.id), 0.0))


def advance_billing_date(current: date, frequency) -> date:
    from .models import BillingFrequency
    if frequency == BillingFrequency.DAILY:
        return current + __import__("datetime").timedelta(days=1)
    if frequency == BillingFrequency.WEEKLY:
        return current + __import__("datetime").timedelta(days=7)
    if frequency == BillingFrequency.MONTHLY:
        return current + __import__("datetime").timedelta(days=30)
    if frequency == BillingFrequency.YEARLY:
        return current + __import__("datetime").timedelta(days=365)
    return current + __import__("datetime").timedelta(days=30)


def subscriptions_due_for_billing(db: Session, on_date: date = None):
    """Active subscriptions whose next_billing_date is on or before on_date."""
    from .models import Subscription, SubscriptionStatus
    on_date = on_date or date.today()
    return (
        db.query(Subscription)
        .filter(
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.next_billing_date != None,
            Subscription.next_billing_date <= on_date,
        )
        .all()
    )


def invoice_already_for_period(db: Session, subscription_id: int, period_date: date) -> bool:
    """True if an invoice already exists for this subscription on period_date."""
    from .models import Invoice, InvoiceStatus
    return (
        db.query(Invoice)
        .filter(
            Invoice.subscription_id == subscription_id,
            Invoice.issue_date == period_date,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
        .first()
        is not None
    )
