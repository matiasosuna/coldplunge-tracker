import os
import secrets
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
from app.models import Transaction, Location, INCOME_CATEGORIES, EXPENSE_CATEGORIES, SocialProfile, SocialLink, SocialPhoto, TodoItem, Session as IceSession, SessionSignup
from app.auth import (
    verify_password, create_session_token, get_current_user,
    COOKIE_NAME, REMEMBER_ME_DAYS
)

# Create tables
models.Base.metadata.create_all(bind=engine)

# Manual migrations — add columns that may not exist in older DBs
def run_migrations():
    with engine.connect() as conn:
        migrations = [
            "ALTER TABLE social_links ADD COLUMN IF NOT EXISTS is_donation BOOLEAN DEFAULT FALSE",
            "ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS note VARCHAR(1000)",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS notes VARCHAR(500)",
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()

from sqlalchemy import text
run_migrations()

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
    return tr(request, "login.html", {"error": "Incorrect password"})


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    if not get_current_user(request):
        return auth_redirect()
    today = date.today()
    total_ingresos = db.query(func.sum(Transaction.amount)).filter(Transaction.type == "ingreso").scalar() or 0.0
    total_gastos = db.query(func.sum(Transaction.amount)).filter(Transaction.type == "gasto").scalar() or 0.0
    pendientes_count = db.query(TodoItem).filter(TodoItem.done == False).count()
    proxima_sesion = db.query(IceSession).filter(IceSession.active == True, IceSession.date >= today).order_by(IceSession.date).first()
    signups_count = db.query(SessionSignup).filter(SessionSignup.session_id == proxima_sesion.id).count() if proxima_sesion else 0
    return tr(request, "home.html", {
        "total_ingresos": total_ingresos,
        "total_gastos": total_gastos,
        "ganancia": total_ingresos - total_gastos,
        "pendientes_count": pendientes_count,
        "proxima_sesion": proxima_sesion,
        "signups_count": signups_count,
        "today": today,
    })


@app.get("/finanzas", response_class=HTMLResponse)
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

    return tr(request, "finanzas.html", {
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
        raise HTTPException(status_code=404, detail="Transaction not found")

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
        raise HTTPException(status_code=404, detail="Transaction not found")

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


@app.get("/pay", response_class=HTMLResponse)
async def pay_page(request: Request, db: Session = Depends(get_db)):
    profile = get_or_create_profile(db)
    donation_links = db.query(SocialLink).filter(
        SocialLink.active == True,
        SocialLink.is_donation == True,
    ).order_by(SocialLink.order).all()
    return tr(request, "pay.html", {
        "profile": profile,
        "donation_links": donation_links,
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
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    profile = get_or_create_profile(db)
    profile.business_name = business_name.strip()
    profile.tagline = tagline.strip()
    profile.profile_image_url = profile_image_url.strip()
    db.commit()
    return RedirectResponse(url="/admin/social", status_code=302)


@app.post("/admin/social/links/add")
async def admin_add_link(
    request: Request,
    platform: str = Form(...),
    label: str = Form(...),
    url: str = Form(...),
    is_donation: Optional[str] = Form(None),
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
        is_donation=is_donation is not None,
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

TODO_CATEGORIES = ["buy", "research", "do"]
TODO_PRIORITIES = ["high", "normal", "low"]

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
    category: str = Form("do"),
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


# ---------------------------------------------------------------------------
# Session signup system
# ---------------------------------------------------------------------------

def check_session_auth(request: Request, session_id: int) -> bool:
    return request.cookies.get(f"sesion_{session_id}_auth") == "ok"


# --- Admin routes ---

@app.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions_page(request: Request, db: Session = Depends(get_db)):
    if not get_current_user(request):
        return auth_redirect()
    sessions = db.query(IceSession).order_by(IceSession.date.desc(), IceSession.time).all()
    return tr(request, "admin_sessions.html", {"sessions": sessions})


@app.post("/admin/sessions/nueva")
async def admin_create_session(
    request: Request,
    title: str = Form("Ice Bath Session"),
    session_date: str = Form(...),
    time: str = Form(...),
    location: str = Form("My Khe Beach"),
    price: str = Form("150,000 VND"),
    max_spots: int = Form(10),
    access_password: str = Form(...),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    sess = IceSession(
        title=title.strip(),
        date=date.fromisoformat(session_date),
        time=time.strip(),
        location=location.strip(),
        price=price.strip(),
        max_spots=max_spots,
        access_password=access_password,
        notes=notes.strip() if notes else None,
    )
    db.add(sess)
    db.commit()
    return RedirectResponse(url="/admin/sessions", status_code=302)


@app.post("/admin/sessions/{session_id}/toggle")
async def admin_toggle_session(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    sess = db.query(IceSession).filter(IceSession.id == session_id).first()
    if sess:
        sess.active = not sess.active
        db.commit()
    return RedirectResponse(url="/admin/sessions", status_code=302)


@app.post("/admin/sessions/{session_id}/delete")
async def admin_delete_session(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    sess = db.query(IceSession).filter(IceSession.id == session_id).first()
    if sess:
        db.delete(sess)
        db.commit()
    return RedirectResponse(url="/admin/sessions", status_code=302)


@app.get("/admin/sessions/{session_id}/signups", response_class=HTMLResponse)
async def admin_session_signups(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    sess = db.query(IceSession).filter(IceSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return tr(request, "admin_session_signups.html", {"sess": sess})


@app.post("/admin/sessions/{session_id}/signups/{signup_id}/delete")
async def admin_delete_signup(
    request: Request,
    session_id: int,
    signup_id: int,
    db: Session = Depends(get_db),
):
    if not get_current_user(request):
        return auth_redirect()
    signup = db.query(SessionSignup).filter(
        SessionSignup.id == signup_id,
        SessionSignup.session_id == session_id,
    ).first()
    if signup:
        db.delete(signup)
        db.commit()
    return RedirectResponse(url=f"/admin/sessions/{session_id}/signups", status_code=302)


# --- Public routes ---

@app.get("/sesion/{session_id}", response_class=HTMLResponse)
async def public_session_page(request: Request, session_id: int, db: Session = Depends(get_db)):
    sess = db.query(IceSession).filter(IceSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    authenticated = check_session_auth(request, session_id)
    signup_count = len(sess.signups)
    spots_remaining = sess.max_spots - signup_count
    # First names only for privacy
    attendee_names = [s.name.split()[0] for s in sess.signups]
    return tr(request, "session_public.html", {
        "sess": sess,
        "authenticated": authenticated,
        "signup_count": signup_count,
        "spots_remaining": spots_remaining,
        "attendee_names": attendee_names,
    })


@app.post("/sesion/{session_id}/auth")
async def public_session_auth(
    request: Request,
    session_id: int,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    sess = db.query(IceSession).filter(IceSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if password == sess.access_password:
        response = RedirectResponse(url=f"/sesion/{session_id}", status_code=302)
        response.set_cookie(
            key=f"sesion_{session_id}_auth",
            value="ok",
            httponly=True,
            max_age=86400,
            samesite="lax",
        )
        return response
    signup_count = len(sess.signups)
    spots_remaining = sess.max_spots - signup_count
    attendee_names = [s.name.split()[0] for s in sess.signups]
    return tr(request, "session_public.html", {
        "sess": sess,
        "authenticated": False,
        "signup_count": signup_count,
        "spots_remaining": spots_remaining,
        "attendee_names": attendee_names,
        "auth_error": "Contraseña incorrecta",
    })


@app.post("/sesion/{session_id}/signup")
async def public_session_signup(
    request: Request,
    session_id: int,
    name: str = Form(...),
    whatsapp: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    sess = db.query(IceSession).filter(IceSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if not check_session_auth(request, session_id):
        return RedirectResponse(url=f"/sesion/{session_id}", status_code=302)
    signup_count = len(sess.signups)
    if not sess.active or signup_count >= sess.max_spots:
        return RedirectResponse(url=f"/sesion/{session_id}", status_code=302)
    cancel_token = secrets.token_urlsafe(32)
    signup = SessionSignup(
        session_id=session_id,
        name=name.strip(),
        whatsapp=whatsapp.strip() if whatsapp else None,
        cancel_token=cancel_token,
    )
    db.add(signup)
    db.commit()
    db.refresh(signup)
    cancel_url = str(request.base_url).rstrip("/") + f"/sesion/{session_id}/cancelar/{cancel_token}"
    return tr(request, "session_confirm.html", {
        "sess": sess,
        "signup": signup,
        "cancel_url": cancel_url,
    })


@app.get("/sesion/{session_id}/cancelar/{cancel_token}", response_class=HTMLResponse)
async def public_cancel_page(
    request: Request,
    session_id: int,
    cancel_token: str,
    db: Session = Depends(get_db),
):
    signup = db.query(SessionSignup).filter(
        SessionSignup.cancel_token == cancel_token,
        SessionSignup.session_id == session_id,
    ).first()
    if not signup:
        raise HTTPException(status_code=404, detail="Link de cancelación no válido")
    return tr(request, "session_cancel.html", {"signup": signup, "sess": signup.session})


@app.post("/sesion/{session_id}/cancelar/{cancel_token}")
async def public_cancel_signup(
    request: Request,
    session_id: int,
    cancel_token: str,
    db: Session = Depends(get_db),
):
    signup = db.query(SessionSignup).filter(
        SessionSignup.cancel_token == cancel_token,
        SessionSignup.session_id == session_id,
    ).first()
    if signup:
        db.delete(signup)
        db.commit()
    return RedirectResponse(url=f"/sesion/{session_id}", status_code=302)
