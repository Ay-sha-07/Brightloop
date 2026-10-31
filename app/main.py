from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, date, timedelta
from typing import Optional
import os

from .database import engine, Base, get_db, SessionLocal
from .models import (
    User, UserRole, Product, ProductVariant, Plan, PlanProduct,
    Subscription, SubscriptionLine, SubscriptionStatus,
    Invoice, InvoiceLine, InvoiceStatus, Payment, TaxRule, Discount,
    BillingFrequency, DiscountType, QuotationTemplate, PasswordResetToken
)
from .auth import (
    get_password_hash, authenticate_user, create_access_token,
    get_current_user, require_user, require_admin, require_staff_or_admin,
    validate_password, get_user_by_email, validate_email
)
from .billing import (
    can_transition_sub, can_transition_inv, compute_invoice_totals,
    mark_discounts_used, amount_paid, amount_due, money, get_default_tax_rate,
    advance_billing_date, subscriptions_due_for_billing, invoice_already_for_period
)
from .schemas import DashboardStats
from .csrf import generate_csrf_token, sign_token, require_csrf, get_csrf_from_request
from jinja2 import Environment, FileSystemLoader


Base.metadata.create_all(bind=engine)


def ensure_schema():
    """Add columns/tables that create_all skips on existing DBs."""
    from sqlalchemy import text as sql_text, inspect
    insp = inspect(engine)
    with engine.begin() as conn:
        cols = [c["name"] for c in insp.get_columns("subscriptions")] if insp.has_table("subscriptions") else []
        if cols and "next_billing_date" not in cols:
            conn.execute(sql_text("ALTER TABLE subscriptions ADD COLUMN next_billing_date DATE"))
        if not insp.has_table("password_reset_tokens"):
            PasswordResetToken.__table__.create(bind=engine, checkfirst=True)


ensure_schema()


app = FastAPI(title="Brightloop Subscription System", version="1.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
_env = Environment(loader=FileSystemLoader(os.path.join(BASE_DIR, "templates")), cache_size=0)
templates = Jinja2Templates(env=_env)


def seed_data():
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == "admin@brightloop.io").first():
            return
        admin = User(
            email="admin@brightloop.io",
            hashed_password=get_password_hash("Admin@1234"),
            full_name="System Admin",
            role=UserRole.ADMIN,
        )
        staff = User(
            email="staff@brightloop.io",
            hashed_password=get_password_hash("Staff@1234"),
            full_name="Internal Staff",
            role=UserRole.STAFF,
        )
        customer = User(
            email="customer@example.com",
            hashed_password=get_password_hash("Customer@1"),
            full_name="Acme Shop",
            role=UserRole.CUSTOMER,
        )
        db.add_all([admin, staff, customer])
        db.flush()

        p1 = Product(name="Brightloop Core", product_type="software", sales_price=49.0, cost_price=12.0, notes="Core platform")
        p2 = Product(name="Analytics Pack", product_type="addon", sales_price=19.0, cost_price=4.0)
        p3 = Product(name="Odoo Connector", product_type="integration", sales_price=29.0, cost_price=8.0)
        db.add_all([p1, p2, p3])
        db.flush()

        db.add(ProductVariant(product_id=p3.id, name="Odoo Brand", price_extra=560.0))
        db.add(ProductVariant(product_id=p1.id, name="Standard", price_extra=0.0))
        db.add(ProductVariant(product_id=p1.id, name="Enterprise", price_extra=120.0))

        plan_m = Plan(
            name="Monthly Starter",
            billing_frequency=BillingFrequency.MONTHLY,
            price=49.0,
            min_quantity=1,
            available_from=date(2025, 1, 1),
            auto_close=False,
            allow_early_close=True,
            allow_pause=True,
            allow_renew=True,
        )
        plan_y = Plan(
            name="Yearly Business",
            billing_frequency=BillingFrequency.YEARLY,
            price=490.0,
            min_quantity=5,
            available_from=date(2025, 1, 1),
            auto_close=True,
            allow_early_close=True,
            allow_pause=False,
            allow_renew=True,
        )
        db.add_all([plan_m, plan_y])
        db.flush()

        db.add(PlanProduct(plan_id=plan_m.id, product_id=p1.id, quantity=1))
        db.add(PlanProduct(plan_id=plan_y.id, product_id=p1.id, quantity=5))
        db.add(PlanProduct(plan_id=plan_y.id, product_id=p2.id, quantity=1))

        db.add(TaxRule(name="Standard VAT", rate=20.0, is_default=True))
        db.add(TaxRule(name="Reduced", rate=5.0))

        db.add(Discount(
            name="Volume Seats",
            discount_type=DiscountType.PERCENTAGE,
            value=10.0,
            min_quantity=10,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
            max_uses=100,
            applies_to_subscription=True,
        ))

        db.commit()
    finally:
        db.close()


seed_data()

def _csrf_pair(response_or_none=None):
    raw = generate_csrf_token()
    signed = sign_token(raw)
    return signed


def inject_csrf(request: Request):
    """Return signed token; set cookie via response in routes that return HTML."""
    existing = get_csrf_from_request(request)
    if existing:
        return existing
    return sign_token(generate_csrf_token())



def next_number(db: Session, prefix: str, model) -> str:
    # Prefer max(id) so deletes/concurrency are less likely to collide
    max_id = db.query(func.max(model.id)).scalar() or 0
    return f"{prefix}-{max_id + 1:05d}"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, user: Optional[User] = Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, email.strip().lower(), password)
    if not user:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid email or password"},
            status_code=401,
        )
    token = create_access_token({"sub": user.email, "role": user.role.value})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=60 * 60 * 12,
        samesite="lax",
    )
    return response


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@app.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    ok_email, email_msg = validate_email(email)
    if not ok_email:
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": email_msg},
            status_code=400,
        )
    if get_user_by_email(db, email):
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": "Email already registered"},
            status_code=400,
        )
    ok, msg = validate_password(password)
    if not ok:
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": msg},
            status_code=400,
        )
    user = User(
        email=email,
        hashed_password=get_password_hash(password),
        full_name=full_name.strip(),
        role=UserRole.CUSTOMER,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.email, "role": user.role.value})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=60 * 60 * 12, samesite="lax")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    today = date.today()
    month_start = today.replace(day=1)
    is_customer = user.role == UserRole.CUSTOMER
    cust_id = user.id if is_customer else None

    # Active subscriptions
    aq = db.query(func.count(Subscription.id)).filter(
        Subscription.status == SubscriptionStatus.ACTIVE
    )
    if cust_id:
        aq = aq.filter(Subscription.customer_id == cust_id)
    active_subs = aq.scalar() or 0

    # Revenue from payments on non-cancelled invoices
    rq = (
        db.query(func.coalesce(func.sum(Payment.amount), 0.0))
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .filter(Invoice.status != InvoiceStatus.CANCELLED)
    )
    if cust_id:
        rq = rq.filter(Invoice.customer_id == cust_id)
    total_revenue = rq.scalar() or 0.0

    # Outstanding: confirmed invoices only
    oq = db.query(func.coalesce(func.sum(Invoice.total), 0.0)).filter(
        Invoice.status == InvoiceStatus.CONFIRMED
    )
    if cust_id:
        oq = oq.filter(Invoice.customer_id == cust_id)
    outstanding = oq.scalar() or 0.0

    # Overdue confirmed invoices
    dq = db.query(func.count(Invoice.id)).filter(
        Invoice.status == InvoiceStatus.CONFIRMED,
        Invoice.due_date < today,
    )
    if cust_id:
        dq = dq.filter(Invoice.customer_id == cust_id)
    overdue = dq.scalar() or 0

    # Payments this month
    pq = (
        db.query(func.coalesce(func.sum(Payment.amount), 0.0))
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .filter(Payment.payment_date >= month_start)
    )
    if cust_id:
        pq = pq.filter(Invoice.customer_id == cust_id)
    payments_month = pq.scalar() or 0.0

    inv_list_q = db.query(Invoice)
    sub_list_q = db.query(Subscription)
    if cust_id:
        inv_list_q = inv_list_q.filter(Invoice.customer_id == cust_id)
        sub_list_q = sub_list_q.filter(Subscription.customer_id == cust_id)
    recent_invoices = inv_list_q.order_by(Invoice.created_at.desc()).limit(8).all()
    recent_subs = sub_list_q.order_by(Subscription.created_at.desc()).limit(8).all()

    stats = {
        "active_subscriptions": active_subs,
        "total_revenue": round(float(total_revenue), 2),
        "outstanding": round(float(outstanding), 2),
        "overdue_count": overdue,
        "payments_this_month": round(float(payments_month), 2),
    }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "stats": stats,
            "recent_invoices": recent_invoices,
            "recent_subs": recent_subs,
        },
    )


@app.get("/products", response_class=HTMLResponse)
async def products_page(
    request: Request,
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    products = db.query(Product).filter(Product.is_active == True).order_by(Product.name).all()
    return templates.TemplateResponse(
        request,
        "products.html",
        {"user": user, "products": products},
    )


@app.post("/products")
async def create_product(
    name: str = Form(...),
    product_type: str = Form("software"),
    sales_price: float = Form(...),
    cost_price: float = Form(0.0),
    notes: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if sales_price < 0 or cost_price < 0:
        raise HTTPException(400, "Prices cannot be negative")
    p = Product(
        name=name.strip(),
        product_type=product_type.strip(),
        sales_price=sales_price,
        cost_price=cost_price,
        notes=notes.strip(),
    )
    db.add(p)
    db.commit()
    return RedirectResponse("/products", status_code=302)


@app.post("/products/{product_id}/variants")
async def add_variant(
    product_id: int,
    name: str = Form(...),
    price_extra: float = Form(0.0),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(404, "Product not found")
    v = ProductVariant(product_id=product_id, name=name.strip(), price_extra=price_extra)
    db.add(v)
    db.commit()
    return RedirectResponse("/products", status_code=302)


@app.get("/plans", response_class=HTMLResponse)
async def plans_page(
    request: Request,
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    plans = db.query(Plan).filter(Plan.is_active == True).order_by(Plan.name).all()
    products = db.query(Product).filter(Product.is_active == True).all()
    return templates.TemplateResponse(
        request,
        "plans.html",
        {"user": user, "plans": plans, "products": products},
    )


@app.post("/plans")
async def create_plan(
    name: str = Form(...),
    billing_frequency: str = Form(...),
    price: float = Form(...),
    min_quantity: int = Form(1),
    auto_close: Optional[str] = Form(None),
    allow_early_close: Optional[str] = Form(None),
    allow_pause: Optional[str] = Form(None),
    allow_renew: Optional[str] = Form(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        freq = BillingFrequency(billing_frequency)
    except ValueError:
        raise HTTPException(400, f"Invalid billing frequency: {billing_frequency}")
    if price < 0:
        raise HTTPException(400, "Price cannot be negative")
    if min_quantity < 1:
        raise HTTPException(400, "Minimum quantity must be at least 1")
    plan = Plan(
        name=name.strip(),
        billing_frequency=freq,
        price=price,
        min_quantity=min_quantity,
        auto_close=auto_close is not None,
        allow_early_close=allow_early_close is not None,
        allow_pause=allow_pause is not None,
        allow_renew=allow_renew is not None,
        available_from=date.today(),
    )
    db.add(plan)
    db.commit()
    return RedirectResponse("/plans", status_code=302)


@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status_filter: Optional[str] = None,
):
    q = db.query(Subscription)
    if user.role == UserRole.CUSTOMER:
        q = q.filter(Subscription.customer_id == user.id)
    if status_filter:
        try:
            q = q.filter(Subscription.status == SubscriptionStatus(status_filter))
        except ValueError:
            pass
    subs = q.order_by(Subscription.created_at.desc()).all()
    customers = []
    plans = []
    if user.role in (UserRole.ADMIN, UserRole.STAFF):
        customers = db.query(User).filter(User.role == UserRole.CUSTOMER).all()
        plans = db.query(Plan).filter(Plan.is_active == True).all()
    return templates.TemplateResponse(
        request,
        "subscriptions.html",
        {
            "user": user,
            "subs": subs,
            "customers": customers,
            "plans": plans,
            "status_filter": status_filter,
        },
    )


@app.post("/subscriptions")
async def create_subscription(
    customer_id: int = Form(...),
    plan_id: int = Form(...),
    payment_terms: str = Form("Net 30"),
    notes: str = Form(""),
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Plan not found")
    cust = db.query(User).filter(User.id == customer_id, User.role == UserRole.CUSTOMER).first()
    if not cust:
        raise HTTPException(404, "Customer not found")

    number = next_number(db, "SUB", Subscription)
    start = date.today()
    if plan.billing_frequency == BillingFrequency.YEARLY:
        expiry = start + timedelta(days=365)
    elif plan.billing_frequency == BillingFrequency.MONTHLY:
        expiry = start + timedelta(days=30)
    elif plan.billing_frequency == BillingFrequency.WEEKLY:
        expiry = start + timedelta(days=7)
    else:
        expiry = start + timedelta(days=1)

    sub = Subscription(
        number=number,
        customer_id=customer_id,
        plan_id=plan_id,
        status=SubscriptionStatus.DRAFT,
        start_date=start,
        expiry_date=expiry,
        payment_terms=payment_terms,
        notes=notes.strip(),
        next_billing_date=start,
    )
    db.add(sub)
    db.flush()

    for pp in plan.products:
        line = SubscriptionLine(
            subscription_id=sub.id,
            product_id=pp.product_id,
            quantity=max(pp.quantity, plan.min_quantity),
            unit_price=pp.product.sales_price,
            tax_rate=get_default_tax_rate(db),
            amount=pp.product.sales_price * max(pp.quantity, plan.min_quantity),
        )
        db.add(line)

    db.commit()
    return RedirectResponse(f"/subscriptions/{sub.id}", status_code=302)


@app.get("/subscriptions/{sub_id}", response_class=HTMLResponse)
async def subscription_detail(
    sub_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not sub:
        raise HTTPException(404, "Not found")
    if user.role == UserRole.CUSTOMER and sub.customer_id != user.id:
        raise HTTPException(403, "Access denied")
    return templates.TemplateResponse(
        request,
        "subscription_detail.html",
        {"user": user, "sub": sub},
    )


@app.post("/subscriptions/{sub_id}/status")
async def update_sub_status(
    sub_id: int,
    new_status: str = Form(...),
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not sub:
        raise HTTPException(404)
    try:
        target = SubscriptionStatus(new_status)
    except ValueError:
        raise HTTPException(400, "Invalid status")
    if not can_transition_sub(sub.status, target):
        raise HTTPException(
            400,
            f"Cannot move subscription from {sub.status.value} to {target.value}",
        )
    sub.status = target
    sub.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/subscriptions/{sub_id}", status_code=302)


@app.post("/subscriptions/{sub_id}/generate-invoice")
async def generate_invoice(
    sub_id: int,
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not sub or sub.status not in (SubscriptionStatus.CONFIRMED, SubscriptionStatus.ACTIVE):
        raise HTTPException(400, "Subscription must be confirmed or active")

    existing = (
        db.query(Invoice)
        .filter(
            Invoice.subscription_id == sub_id,
            Invoice.status.in_([InvoiceStatus.DRAFT, InvoiceStatus.CONFIRMED]),
            Invoice.issue_date == date.today(),
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "Invoice already generated for today")

    number = next_number(db, "INV", Invoice)
    default_rate = get_default_tax_rate(db)

    lines_data = []
    line_amounts = []
    total_qty = 0
    for line in sub.lines:
        amt = money(line.unit_price * line.quantity)
        line_amounts.append(amt)
        total_qty += line.quantity
        lines_data.append({
            "product_id": line.product_id,
            "description": line.product.name,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "tax_rate": line.tax_rate if line.tax_rate is not None else default_rate,
            "amount": amt,
        })

    totals = compute_invoice_totals(
        db,
        line_amounts=line_amounts,
        total_quantity=total_qty,
    )

    inv = Invoice(
        number=number,
        subscription_id=sub.id,
        customer_id=sub.customer_id,
        status=InvoiceStatus.DRAFT,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=30),
        subtotal=totals["subtotal"],
        tax_amount=totals["tax_amount"],
        discount_amount=totals["discount_amount"],
        total=totals["total"],
    )
    db.add(inv)
    db.flush()

    for ld in lines_data:
        il = InvoiceLine(
            invoice_id=inv.id,
            product_id=ld["product_id"],
            description=ld["description"],
            quantity=ld["quantity"],
            unit_price=ld["unit_price"],
            tax_rate=ld["tax_rate"],
            amount=ld["amount"],
        )
        db.add(il)

    if totals["discounts_used"]:
        mark_discounts_used(db, totals["discounts_used"])

    if sub.status == SubscriptionStatus.CONFIRMED:
        sub.status = SubscriptionStatus.ACTIVE
    # Advance next billing date so the same period is not billed twice
    if sub.next_billing_date:
        sub.next_billing_date = advance_billing_date(sub.next_billing_date, sub.plan.billing_frequency)
    else:
        sub.next_billing_date = advance_billing_date(date.today(), sub.plan.billing_frequency)
    db.commit()
    return RedirectResponse(f"/invoices/{inv.id}", status_code=302)


@app.get("/invoices", response_class=HTMLResponse)
async def invoices_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status_filter: Optional[str] = None,
):
    q = db.query(Invoice)
    if user.role == UserRole.CUSTOMER:
        q = q.filter(Invoice.customer_id == user.id)
    if status_filter:
        try:
            q = q.filter(Invoice.status == InvoiceStatus(status_filter))
        except ValueError:
            pass
    invoices = q.order_by(Invoice.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "invoices.html",
        {"user": user, "invoices": invoices, "status_filter": status_filter},
    )


@app.get("/invoices/{inv_id}", response_class=HTMLResponse)
async def invoice_detail(
    inv_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(404)
    if user.role == UserRole.CUSTOMER and inv.customer_id != user.id:
        raise HTTPException(403)
    return templates.TemplateResponse(
        request,
        "invoice_detail.html",
        {"user": user, "inv": inv},
    )


@app.post("/invoices/{inv_id}/status")
async def update_invoice_status(
    inv_id: int,
    new_status: str = Form(...),
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(404)
    try:
        target = InvoiceStatus(new_status)
    except ValueError:
        raise HTTPException(400, "Invalid status")
    if not can_transition_inv(inv.status, target):
        raise HTTPException(
            400,
            f"Cannot move invoice from {inv.status.value} to {target.value}",
        )
    inv.status = target
    db.commit()
    return RedirectResponse(f"/invoices/{inv_id}", status_code=302)


@app.post("/invoices/{inv_id}/pay")
async def record_payment(
    inv_id: int,
    amount: float = Form(...),
    method: str = Form(...),
    note: str = Form(""),
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(404)
    if inv.status in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED):
        raise HTTPException(400, f"Cannot record payment on a {inv.status.value} invoice")
    if inv.status == InvoiceStatus.DRAFT:
        raise HTTPException(400, "Confirm the invoice before recording payment")
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    due = amount_due(db, inv)
    if amount > due + 0.01:
        raise HTTPException(400, f"Payment exceeds amount due ({due:.2f})")
    pay = Payment(
        invoice_id=inv.id,
        amount=money(amount),
        method=method.strip(),
        payment_date=date.today(),
        note=note.strip(),
        recorded_by_id=user.id,
    )
    db.add(pay)
    db.flush()
    paid_total = amount_paid(db, inv.id)
    if paid_total >= float(inv.total) - 0.01:
        inv.status = InvoiceStatus.PAID
    # stay CONFIRMED if partial
    db.commit()
    return RedirectResponse(f"/invoices/{inv_id}", status_code=302)


@app.get("/discounts", response_class=HTMLResponse)
async def discounts_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    discounts = db.query(Discount).order_by(Discount.created_at.desc()).all()
    products = db.query(Product).filter(Product.is_active == True).all()
    return templates.TemplateResponse(
        request,
        "discounts.html",
        {"user": user, "discounts": discounts, "products": products},
    )


@app.post("/discounts")
async def create_discount(
    name: str = Form(...),
    discount_type: str = Form(...),
    value: float = Form(...),
    min_purchase: float = Form(0.0),
    min_quantity: int = Form(0),
    max_uses: Optional[int] = Form(None),
    applies_to_subscription: bool = Form(False),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        dtype = DiscountType(discount_type)
    except ValueError:
        raise HTTPException(400, "Invalid discount type")
    if value < 0:
        raise HTTPException(400, "Discount value cannot be negative")
    if dtype == DiscountType.PERCENTAGE and value > 100:
        raise HTTPException(400, "Percentage discount cannot exceed 100")
    d = Discount(
        name=name.strip(),
        discount_type=dtype,
        value=value,
        min_purchase=min_purchase,
        min_quantity=min_quantity,
        max_uses=max_uses,
        applies_to_subscription=applies_to_subscription,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=365),
    )
    db.add(d)
    db.commit()
    return RedirectResponse("/discounts", status_code=302)


@app.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "users.html",
        {"user": user, "users": users},
    )


@app.post("/users")
async def add_staff(
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form("staff"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if get_user_by_email(db, email):
        raise HTTPException(400, "Email already exists")
    ok, msg = validate_password(password)
    if not ok:
        raise HTTPException(400, msg)
    role = (role or "").strip().lower()
    if role not in ("staff", "admin"):
        raise HTTPException(400, "Role must be 'staff' or 'admin'")
    ok_email, email_msg = validate_email(email)
    if not ok_email:
        raise HTTPException(400, email_msg)
    new_role = UserRole.STAFF if role == "staff" else UserRole.ADMIN
    u = User(
        email=email,
        hashed_password=get_password_hash(password),
        full_name=full_name.strip(),
        role=new_role,
    )
    db.add(u)
    db.commit()
    return RedirectResponse("/users", status_code=302)


@app.get("/api/stats", response_model=DashboardStats)
async def api_stats(
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    active = db.query(func.count(Subscription.id)).filter(
        Subscription.status == SubscriptionStatus.ACTIVE
    ).scalar() or 0
    revenue = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).scalar() or 0.0
    outstanding = db.query(func.coalesce(func.sum(Invoice.total), 0.0)).filter(
        Invoice.status.in_([InvoiceStatus.DRAFT, InvoiceStatus.CONFIRMED])
    ).scalar() or 0.0
    overdue = db.query(func.count(Invoice.id)).filter(
        and_(Invoice.status == InvoiceStatus.CONFIRMED, Invoice.due_date < date.today())
    ).scalar() or 0
    month_start = date.today().replace(day=1)
    pay_month = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
        Payment.payment_date >= month_start
    ).scalar() or 0.0
    return DashboardStats(
        active_subscriptions=active,
        total_revenue=round(revenue, 2),
        outstanding=round(outstanding, 2),
        overdue_count=overdue,
        payments_this_month=round(pay_month, 2),
    )


# ---------- Password reset ----------

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    token = inject_csrf(request)
    resp = templates.TemplateResponse(
        request, "forgot_password.html",
        {"error": None, "message": None, "csrf_token": token},
    )
    resp.set_cookie("csrf_token", token, httponly=True, samesite="lax", max_age=3600)
    return resp


@app.post("/forgot-password")
async def forgot_password(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    token = inject_csrf(request)
    email = email.strip().lower()
    user = get_user_by_email(db, email)
    # Always show success to avoid email enumeration
    message = "If that email is registered, a reset link was generated. Check the server console."
    if user:
        import secrets
        raw = secrets.token_urlsafe(32)
        prt = PasswordResetToken(
            user_id=user.id,
            token=raw,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.add(prt)
        db.commit()
        link = f"/reset-password/{raw}"
        print(f"[PASSWORD RESET] {user.email} → http://localhost:8000{link}")
    resp = templates.TemplateResponse(
        request, "forgot_password.html",
        {"error": None, "message": message, "csrf_token": token},
    )
    resp.set_cookie("csrf_token", token, httponly=True, samesite="lax", max_age=3600)
    return resp


@app.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(token: str, request: Request, db: Session = Depends(get_db)):
    prt = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used == False,
        PasswordResetToken.expires_at > datetime.utcnow(),
    ).first()
    csrf = inject_csrf(request)
    if not prt:
        return templates.TemplateResponse(
            request, "forgot_password.html",
            {"error": "Reset link is invalid or expired.", "message": None, "csrf_token": csrf},
        )
    resp = templates.TemplateResponse(
        request, "reset_password.html",
        {"error": None, "token": token, "csrf_token": csrf},
    )
    resp.set_cookie("csrf_token", csrf, httponly=True, samesite="lax", max_age=3600)
    return resp


@app.post("/reset-password/{token}")
async def reset_password(
    token: str,
    request: Request,
    password: str = Form(...),
    password2: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    csrf = inject_csrf(request)
    prt = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used == False,
        PasswordResetToken.expires_at > datetime.utcnow(),
    ).first()
    if not prt:
        return templates.TemplateResponse(
            request, "forgot_password.html",
            {"error": "Reset link is invalid or expired.", "message": None, "csrf_token": csrf},
            status_code=400,
        )
    if password != password2:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"error": "Passwords do not match", "token": token, "csrf_token": csrf},
            status_code=400,
        )
    ok, msg = validate_password(password)
    if not ok:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"error": msg, "token": token, "csrf_token": csrf},
            status_code=400,
        )
    user = db.query(User).filter(User.id == prt.user_id).first()
    user.hashed_password = get_password_hash(password)
    prt.used = True
    db.commit()
    return RedirectResponse("/login", status_code=302)


# ---------- Tax rules ----------

@app.get("/tax-rules", response_class=HTMLResponse)
async def tax_rules_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rules = db.query(TaxRule).order_by(TaxRule.id).all()
    csrf = inject_csrf(request)
    resp = templates.TemplateResponse(
        request, "tax_rules.html",
        {"user": user, "rules": rules, "csrf_token": csrf},
    )
    resp.set_cookie("csrf_token", csrf, httponly=True, samesite="lax", max_age=3600)
    return resp


@app.post("/tax-rules")
async def create_tax_rule(
    request: Request,
    name: str = Form(...),
    rate: float = Form(...),
    is_default: Optional[str] = Form(None),
    csrf_token: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    if rate < 0 or rate > 100:
        raise HTTPException(400, "Rate must be between 0 and 100")
    if is_default is not None:
        for r in db.query(TaxRule).filter(TaxRule.is_default == True).all():
            r.is_default = False
    rule = TaxRule(name=name.strip(), rate=rate, is_default=is_default is not None)
    db.add(rule)
    db.commit()
    return RedirectResponse("/tax-rules", status_code=302)


# ---------- Quotation templates ----------

@app.get("/quotation-templates", response_class=HTMLResponse)
async def quotation_templates_page(
    request: Request,
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    templates_list = db.query(QuotationTemplate).filter(QuotationTemplate.is_active == True).all()
    plans = db.query(Plan).filter(Plan.is_active == True).all()
    csrf = inject_csrf(request)
    resp = templates.TemplateResponse(
        request, "quotation_templates.html",
        {"user": user, "templates_list": templates_list, "plans": plans, "csrf_token": csrf},
    )
    resp.set_cookie("csrf_token", csrf, httponly=True, samesite="lax", max_age=3600)
    return resp


@app.post("/quotation-templates")
async def create_quotation_template(
    request: Request,
    name: str = Form(...),
    plan_id: int = Form(...),
    validity_days: int = Form(30),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    if validity_days < 1:
        raise HTTPException(400, "Validity must be at least 1 day")
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Plan not found")
    qt = QuotationTemplate(
        name=name.strip(),
        plan_id=plan_id,
        validity_days=validity_days,
        notes=notes.strip(),
    )
    db.add(qt)
    db.commit()
    return RedirectResponse("/quotation-templates", status_code=302)


# ---------- Bulk billing run ----------

@app.get("/billing", response_class=HTMLResponse)
async def billing_page(
    request: Request,
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    due = subscriptions_due_for_billing(db)
    csrf = inject_csrf(request)
    resp = templates.TemplateResponse(
        request, "billing_run.html",
        {"user": user, "due": due, "results": None, "csrf_token": csrf},
    )
    resp.set_cookie("csrf_token", csrf, httponly=True, samesite="lax", max_age=3600)
    return resp


@app.post("/billing/run")
async def billing_run(
    request: Request,
    csrf_token: str = Form(""),
    user: User = Depends(require_staff_or_admin),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    due = subscriptions_due_for_billing(db)
    results = []
    today = date.today()
    for sub in due:
        if invoice_already_for_period(db, sub.id, today):
            results.append(f"{sub.number}: skipped (already invoiced today)")
            continue
        try:
            default_rate = get_default_tax_rate(db)
            lines_data = []
            line_amounts = []
            total_qty = 0
            for line in sub.lines:
                amt = money(line.unit_price * line.quantity)
                line_amounts.append(amt)
                total_qty += line.quantity
                lines_data.append({
                    "product_id": line.product_id,
                    "description": line.product.name,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_rate": line.tax_rate if line.tax_rate is not None else default_rate,
                    "amount": amt,
                })
            totals = compute_invoice_totals(db, line_amounts=line_amounts, total_quantity=total_qty)
            number = next_number(db, "INV", Invoice)
            inv = Invoice(
                number=number,
                subscription_id=sub.id,
                customer_id=sub.customer_id,
                status=InvoiceStatus.DRAFT,
                issue_date=today,
                due_date=today + timedelta(days=30),
                subtotal=totals["subtotal"],
                tax_amount=totals["tax_amount"],
                discount_amount=totals["discount_amount"],
                total=totals["total"],
            )
            db.add(inv)
            db.flush()
            for ld in lines_data:
                db.add(InvoiceLine(
                    invoice_id=inv.id,
                    product_id=ld["product_id"],
                    description=ld["description"],
                    quantity=ld["quantity"],
                    unit_price=ld["unit_price"],
                    tax_rate=ld["tax_rate"],
                    amount=ld["amount"],
                ))
            if totals["discounts_used"]:
                mark_discounts_used(db, totals["discounts_used"])
            sub.next_billing_date = advance_billing_date(
                sub.next_billing_date or today, sub.plan.billing_frequency
            )
            results.append(f"{sub.number}: invoice {number} created ({totals['total']:.2f})")
        except Exception as e:
            results.append(f"{sub.number}: error — {e}")
    db.commit()
    due_after = subscriptions_due_for_billing(db)
    csrf = inject_csrf(request)
    resp = templates.TemplateResponse(
        request, "billing_run.html",
        {"user": user, "due": due_after, "results": results, "csrf_token": csrf},
    )
    resp.set_cookie("csrf_token", csrf, httponly=True, samesite="lax", max_age=3600)
    return resp


# ---------- Invoice print ----------

@app.get("/invoices/{inv_id}/print", response_class=HTMLResponse)
async def invoice_print(
    inv_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(404)
    if user.role == UserRole.CUSTOMER and inv.customer_id != user.id:
        raise HTTPException(403)
    return templates.TemplateResponse(
        request, "invoice_print.html", {"user": user, "inv": inv}
    )
