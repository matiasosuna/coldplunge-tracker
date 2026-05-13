from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class TransactionType(str, enum.Enum):
    ingreso = "ingreso"
    gasto = "gasto"


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    address = Column(String(500), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="location")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, default=date.today)
    type = Column(String(10), nullable=False)  # "ingreso" or "gasto"
    category = Column(String(100), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    amount = Column(Float, nullable=False)
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    location = relationship("Location", back_populates="transactions")


# Hard-coded categories
INCOME_CATEGORIES = ["Sesión", "Evento", "Otro ingreso"]
EXPENSE_CATEGORIES = ["Hielo", "Agua", "Transporte", "Equipamiento", "Mantenimiento", "Marketing", "Otro gasto"]
ALL_CATEGORIES = INCOME_CATEGORIES + EXPENSE_CATEGORIES
