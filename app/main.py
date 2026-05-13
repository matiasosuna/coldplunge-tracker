import os
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from app.database import engine, get_db
from app import models
from app.models import Transaction, Location, INCOME_CATEGORIES, EXPENSE_CATEGORIES, SocialProfile, SocialLink, SocialPhoto, TodoItem
from app.auth import (
    verify_password, create_session_token, get_current_user,
    COOKIE_NAME, REMEMBER_ME_DAYS
)

# Create tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Cold Plunge Tracker")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))
# Inject categories into Jinja2 env so every template can access them
templates.env.globals["income_categories"] = INCOME_CATEGORIES
templates.env.globals["expense_categories"] = EXPENSE_CATEGORIES


def tr(request: Request, name: str, context: dict = None):
    """Shorthand TemplateResponse compatible with both old and new Starlette."""
    ctx = context or {}
    # Starlette >= 0.28 uses (request, name, context); older uses (name, context_with_request)
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx)
    except TypeError:
        ctx["request"] = request
        return templates.TemplateResponse(name, ctx)


def auth_redirect():
    return RedirectResponse(url="/login", status_code=302)


def get_active_locations(db: Session):
    return db.query(Location).filter(Location.active == True).order_by(Location.name).all()


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=302)
    return tr(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    password: str = Form(...),
    recordarme: Optional[str] = Form(None),
):
    if verify_password(password):
        token = create_session_token()
        response = RedirectResponse(url="/", status_code=302)
        remember = recordarme is not None
        max_age = REMEMBER_ME_DAYS * 24 * 3600 if remember else None
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            max_age=max_age,
            samesite="lax",
        )
        return response
    return tr(request, "login.html", {"error": "Contraseña incorrecta"})


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    today = date.today()
    sel_month = month or today.month
    sel_year = year or today.year

    def monthly_total(tx_type: str):
        result = (
            db.query(func.sum(Transaction.amount))
            .filter(
                Transaction.type == tx_type,
                extract("month", Transaction.date) == sel_month,
                extract("year", Transaction.date) == sel_year,
            )
            .scalar()
        )
        return result or 0.0

    ingresos_mes = monthly_total("ingreso")
    gastos_mes = monthly_total("gasto")
    ganancia_mes = ingresos_mes - gastos_mes

    total_ingresos = db.query(func.sum(Transaction.amount)).filter(Transaction.type == "ingreso").scalar() or 0.0
    total_gastos = db.query(func.sum(Transaction.amount)).filter(Transaction.type == "gasto").scalar() or 0.0
    total_acumulado = total_ingresos - total_gastos

    recent = (
        db.query(Transaction)
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .limit(10)
        .all()
    )

    locations = get_active_locations(db)

    # Build last 12 months list as (year, month) tuples, oldest first
    months = []
    for i in range(11, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    return tr(request, "dashboard.html", {
        "ingresos_mes": ingresos_mes,
        "gastos_mes": gastos_mes,
        "ganancia_mes": ganancia_mes,
        "total_acumulado": total_acumulado,
        "recent": recent,
        "locations": locations,
        "sel_month": sel_month,
        "sel_year": sel_year,
        "months": months,
        "today": today,
    })


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@app.get("/transacciones", response_class=HTMLResponse)
async def transactions_page(
    request: Request,
    month: Optional[int] = None,
    year: Optional[int] = None,
    tipo: Optional[str] = None,
    location_id: Optional[int] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    today = date.today()
    query = db.query(Transaction)

    if month:
        query = query.filter(extract("month", Transaction.date) == month)
    if year:
        query = query.filter(extract("year", Transaction.date) == year)
    if tipo in ("ingreso", "gasto"):
        query = query.filter(Transaction.type == tipo)
    if location_id:
        query = query.filter(Transaction.location_id == location_id)
    if category:
        query = query.filter(Transaction.category == category)

    transactions = query.order_by(Transaction.date.desc(), Transaction.created_at.desc()).all()
    locations = db.query(Location).order_by(Location.name).all()

    return tr(request, "transactions.html", {
        "transactions": transactions,
        "locations": locations,
        "active_locations": [loc for loc in locations if loc.active],
        "sel_month": month or "",
        "sel_year": year or today.year,
        "sel_tipo": tipo or "",
        "sel_location": location_id or "",
        "sel_category": category or "",
        "today": today,
    })


@app.post("/transacciones/nueva")
async def new_transaction(
    request: Request,
    tx_date: str = Form(...),
    tipo: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    location_id: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    redirect_to: str = Form("/transacciones"),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    loc_id = int(location_id) if location_id and location_id.strip() else None
    tx = Transaction(
        date=date.fromisoformat(tx_date),
        type=tipo,
        category=category,
        location_id=loc_id,
        amount=abs(amount),
        note=note or None,
    )
    db.add(tx)
    db.commit()
    return RedirectResponse(url=redirect_to, status_code=302)


@app.get("/transacciones/{tx_id}/editar", response_class=HTMLResponse)
async def edit_transaction_page(
    request: Request,
    tx_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")

    locations = get_active_locations(db)
    return tr(request, "edit_transaction.html", {"tx": tx, "locations": locations})


@app.post("/transacciones/{tx_id}/editar")
async def edit_transaction_post(
    request: Request,
    tx_id: int,
    tx_date: str = Form(...),
    tipo: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    location_id: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")

    tx.date = date.fromisoformat(tx_date)
    tx.type = tipo
    tx.category = category
    tx.amount = abs(amount)
    tx.location_id = int(location_id) if location_id and location_id.strip() else None
    tx.note = note or None
    db.commit()
    return RedirectResponse(url="/transacciones", status_code=302)


@app.post("/transacciones/{tx_id}/eliminar")
async def delete_transaction(
    request: Request,
    tx_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if tx:
        db.delete(tx)
        db.commit()
    return RedirectResponse(url="/transacciones", status_code=302)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

@app.get("/ubicaciones", response_class=HTMLResponse)
async def locations_page(request: Request, db: Session = Depends(get_db)):
    if not get_current_user(request):
        return auth_redirect()

    locations = db.query(Location).order_by(Location.active.desc(), Location.name).all()

    loc_stats = []
    for loc in locations:
        ingresos = (
            db.query(func.sum(Transaction.amount))
            .filter(Transaction.location_id == loc.id, Transaction.type == "ingreso")
            .scalar() or 0.0
        )
        gastos = (
            db.query(func.sum(Transaction.amount))
            .filter(Transaction.location_id == loc.id, Transaction.type == "gasto")
            .scalar() or 0.0
        )
        loc_stats.append({
            "location": loc,
            "ingresos": ingresos,
            "gastos": gastos,
            "ganancia": ingresos - gastos,
        })

    return tr(request, "locations.html", {"loc_stats": loc_stats})


@app.post("/ubicaciones/nueva")
async def new_location(
    request: Request,
    name: str = Form(...),
    address: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    loc = Location(name=name.strip(), address=address.strip() if address else None)
    db.add(loc)
    db.commit()
    return RedirectResponse(url="/ubicaciones", status_code=302)


@app.post("/ubicaciones/{loc_id}/editar")
async def edit_location(
    request: Request,
    loc_id: int,
    name: str = Form(...),
    address: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    loc = db.query(Location).filter(Location.id == loc_id).first()
    if not loc:
        raise HTTPException(status_code=404)
    loc.name = name.strip()
    loc.address = address.strip() if address else None
    db.commit()
    return RedirectResponse(url="/ubicaciones", status_code=302)


@app.post("/ubicaciones/{loc_id}/toggle")
async def toggle_location(
    request: Request,
    loc_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()

    loc = db.query(Location).filter(Location.id == loc_id).first()
    if loc:
        loc.active = not loc.active
        db.commit()
    return RedirectResponse(url="/ubicaciones", status_code=302)


# ---------------------------------------------------------------------------
# Social landing page helpers
# ---------------------------------------------------------------------------

def get_or_create_profile(db: Session) -> SocialProfile:
    profile = db.query(SocialProfile).first()
    if not profile:
        profile = SocialProfile()
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


# ---------------------------------------------------------------------------
# Public social landing page
# ---------------------------------------------------------------------------

@app.get("/social", response_class=HTMLResponse)
async def social_page(request: Request, db: Session = Depends(get_db)):
    profile = get_or_create_profile(db)
    links = db.query(SocialLink).filter(SocialLink.active == True).order_by(SocialLink.order).all()
    photos = db.query(SocialPhoto).order_by(SocialPhoto.order).all()
    return tr(request, "social.html", {
        "profile": profile,
        "links": links,
        "photos": photos,
    })


# ---------------------------------------------------------------------------
# Admin social panel
# ---------------------------------------------------------------------------

@app.get("/admin/social", response_class=HTMLResponse)
async def admin_social_page(request: Request, db: Session = Depends(get_db)):
    if not get_current_user(request):
        return auth_redirect()
    profile = get_or_create_profile(db)
    links = db.query(SocialLink).order_by(SocialLink.order).all()
    photos = db.query(SocialPhoto).order_by(SocialPhoto.order).all()
    return tr(request, "admin_social.html", {
        "profile": profile,
        "links": links,
        "photos": photos,
    })


@app.post("/admin/social/profile")
async def admin_social_profile(
    request: Request,
    business_name: str = Form(...),
    tagline: str = Form(""),
    profile_image_url: str = Form(""),
    donation_link: str = Form(""),
    donation_label: str = Form("Apoyanos"),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    profile = get_or_create_profile(db)
    profile.business_name = business_name.strip()
    profile.tagline = tagline.strip()
    profile.profile_image_url = profile_image_url.strip()
    profile.donation_link = donation_link.strip()
    profile.donation_label = donation_label.strip() or "Apoyanos"
    db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


@app.post("/admin/social/links/add")
async def admin_add_link(
    request: Request,
    platform: str = Form(...),
    label: str = Form(...),
    url: str = Form(...),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    max_order = db.query(SocialLink).count()
    link = SocialLink(
        platform=platform.strip(),
        label=label.strip(),
        url=url.strip(),
        order=max_order,
    )
    db.add(link)
    db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


@app.post("/admin/social/links/{link_id}/delete")
async def admin_delete_link(
    request: Request,
    link_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    link = db.query(SocialLink).filter(SocialLink.id == link_id).first()
    if link:
        db.delete(link)
        db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


@app.post("/admin/social/links/{link_id}/toggle")
async def admin_toggle_link(
    request: Request,
    link_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    link = db.query(SocialLink).filter(SocialLink.id == link_id).first()
    if link:
        link.active = not link.active
        db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


@app.post("/admin/social/photos/add")
async def admin_add_photo(
    request: Request,
    image_url: str = Form(...),
    caption: str = Form(""),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    max_order = db.query(SocialPhoto).count()
    photo = SocialPhoto(
        image_url=image_url.strip(),
        caption=caption.strip(),
        order=max_order,
    )
    db.add(photo)
    db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


@app.post("/admin/social/photos/{photo_id}/delete")
async def admin_delete_photo(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    photo = db.query(SocialPhoto).filter(SocialPhoto.id == photo_id).first()
    if photo:
        db.delete(photo)
        db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


# ---------------------------------------------------------------------------
# Pendientes (Todo)
# ---------------------------------------------------------------------------

TODO_CATEGORIES = ["comprar", "investigar", "hacer"]
TODO_PRIORITIES = ["alta", "normal", "baja"]

@app.get("/pendientes", response_class=HTMLResponse)
async def pendientes_page(
    request: Request,
    categoria: Optional[str] = None,
    estado: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    query = db.query(TodoItem)
    if categoria:
        query = query.filter(TodoItem.category == categoria)
    if estado == "pendiente":
        query = query.filter(TodoItem.done == False)
    elif estado == "hecho":
        query = query.filter(TodoItem.done == True)
    items = query.order_by(TodoItem.done.asc(), TodoItem.created_at.desc()).all()
    return tr(request, "pendientes.html", {
        "items": items,
        "categoria": categoria or "",
        "estado": estado or "",
        "todo_categories": TODO_CATEGORIES,
        "todo_priorities": TODO_PRIORITIES,
    })


@app.post("/pendientes/nuevo")
async def nuevo_pendiente(
    request: Request,
    title: str = Form(...),
    category: str = Form("hacer"),
    priority: str = Form("normal"),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    item = TodoItem(
        title=title.strip(),
        category=category,
        priority=priority,
        note=note.strip() if note else None,
    )
    db.add(item)
    db.commit()
    return RedirectResponse(url="/pendientes", status_code=302)


@app.post("/pendientes/{item_id}/toggle")
async def toggle_pendiente(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    item = db.query(TodoItem).filter(TodoItem.id == item_id).first()
    if item:
        item.done = not item.done
        db.commit()
    return RedirectResponse(url="/pendientes", status_code=302)


@app.post("/pendientes/{item_id}/delete")
async def delete_pendiente(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    item = db.query(TodoItem).filter(TodoItem.id == item_id).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(url="/pendientes", status_code=302)
