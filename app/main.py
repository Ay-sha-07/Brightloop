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
    BillingFrequency, DiscountType
)
from .auth import (
    get_password_hash, authenticate_user, create_access_token,
    get_current_user, require_user, require_admin, require_staff_or_admin,
    validate_password, get_user_by_email
)
from .schemas import DashboardStats
from jinja2 import Environment, FileSystemLoader

Base.metadata.create_all(bind=engine)

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


def next_number(db: Session, prefix: str, model) -> str:
    count = db.query(func.count(model.id)).scalar() or 0
    return f"{prefix}-{count + 1:05d}"


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
    active_subs = db.query(func.count(Subscription.id)).filter(
        Subscription.status == SubscriptionStatus.ACTIVE
    ).scalar() or 0

    total_revenue = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).scalar() or 0.0

    outstanding = db.query(func.coalesce(func.sum(Invoice.total), 0.0)).filter(
        Invoice.status.in_([InvoiceStatus.DRAFT, InvoiceStatus.CONFIRMED])
    ).scalar() or 0.0

    today = date.today()
    overdue = db.query(func.count(Invoice.id)).filter(
        and_(
            Invoice.status == InvoiceStatus.CONFIRMED,
            Invoice.due_date < today,
        )
    ).scalar() or 0

    month_start = today.replace(day=1)
    payments_month = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
        Payment.payment_date >= month_start
    ).scalar() or 0.0

    recent_invoices = (
        db.query(Invoice)
        .order_by(Invoice.created_at.desc())
        .limit(8)
        .all()
    )
    recent_subs = (
        db.query(Subscription)
        .order_by(Subscription.created_at.desc())
        .limit(8)
        .all()
    )

    stats = {
        "active_subscriptions": active_subs,
        "total_revenue": round(total_revenue, 2),
        "outstanding": round(outstanding, 2),
        "overdue_count": overdue,
        "payments_this_month": round(payments_month, 2),
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
    auto_close: bool = Form(False),
    allow_early_close: bool = Form(True),
    allow_pause: bool = Form(True),
    allow_renew: bool = Form(True),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = Plan(
        name=name.strip(),
        billing_frequency=BillingFrequency(billing_frequency),
        price=price,
        min_quantity=min_quantity,
        auto_close=auto_close,
        allow_early_close=allow_early_close,
        allow_pause=allow_pause,
        allow_renew=allow_renew,
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
        status=SubscriptionStatus.QUOTATION,
        start_date=start,
        expiry_date=expiry,
        payment_terms=payment_terms,
        notes=notes.strip(),
    )
    db.add(sub)
    db.flush()

    for pp in plan.products:
        line = SubscriptionLine(
            subscription_id=sub.id,
            product_id=pp.product_id,
            quantity=max(pp.quantity, plan.min_quantity),
            unit_price=pp.product.sales_price,
            tax_rate=20.0,
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
        sub.status = SubscriptionStatus(new_status)
        sub.updated_at = datetime.utcnow()
        db.commit()
    except ValueError:
        raise HTTPException(400, "Invalid status")
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
    tax_rule = db.query(TaxRule).filter(TaxRule.is_default == True).first()
    default_rate = tax_rule.rate if tax_rule else 20.0

    subtotal = 0.0
    lines_data = []
    for line in sub.lines:
        amt = line.unit_price * line.quantity
        subtotal += amt
        lines_data.append({
            "product_id": line.product_id,
            "description": line.product.name,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "tax_rate": line.tax_rate or default_rate,
            "amount": amt,
        })

    tax_amount = subtotal * (default_rate / 100.0)
    total = subtotal + tax_amount

    inv = Invoice(
        number=number,
        subscription_id=sub.id,
        customer_id=sub.customer_id,
        status=InvoiceStatus.DRAFT,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=30),
        subtotal=round(subtotal, 2),
        tax_amount=round(tax_amount, 2),
        discount_amount=0.0,
        total=round(total, 2),
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

    if sub.status == SubscriptionStatus.CONFIRMED:
        sub.status = SubscriptionStatus.ACTIVE
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
        inv.status = InvoiceStatus(new_status)
        db.commit()
    except ValueError:
        raise HTTPException(400)
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
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    pay = Payment(
        invoice_id=inv.id,
        amount=amount,
        method=method.strip(),
        payment_date=date.today(),
        note=note.strip(),
        recorded_by_id=user.id,
    )
    db.add(pay)
    paid_total = db.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
        Payment.invoice_id == inv.id
    ).scalar() or 0.0
    paid_total += amount
    if paid_total >= inv.total - 0.01:
        inv.status = InvoiceStatus.PAID
    else:
        inv.status = InvoiceStatus.CONFIRMED
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
    d = Discount(
        name=name.strip(),
        discount_type=DiscountType(discount_type),
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
