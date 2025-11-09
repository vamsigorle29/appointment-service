"""Database models and schemas"""
from sqlalchemy import Column, Integer, String, DateTime, Integer as SQLInteger
from sqlalchemy.sql import func
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum

from database import Base

class AppointmentStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"

class Appointment(Base):
    __tablename__ = "appointments"
    
    appointment_id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, nullable=False, index=True)
    doctor_id = Column(Integer, nullable=False, index=True)
    department = Column(String, nullable=False)
    slot_start = Column(DateTime, nullable=False, index=True)
    slot_end = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="SCHEDULED")
    reschedule_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    department: str
    slot_start: datetime
    slot_end: datetime

class AppointmentUpdate(BaseModel):
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    status: Optional[AppointmentStatus] = None

class AppointmentResponse(BaseModel):
    appointment_id: int
    patient_id: int
    doctor_id: int
    department: str
    slot_start: datetime
    slot_end: datetime
    status: str
    reschedule_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True

