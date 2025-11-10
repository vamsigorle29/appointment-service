"""
Appointment Service - Handles booking, rescheduling, and cancellation
"""
from fastapi import FastAPI, HTTPException, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, timedelta
from typing import List, Optional
import structlog
import httpx
import os
from uuid import uuid4

from database import get_db, init_db
from models import (
    Appointment, AppointmentCreate, AppointmentUpdate,
    AppointmentResponse, AppointmentStatus
)

logger = structlog.get_logger()

app = FastAPI(
    title="Appointment Service",
    version="v1",
    description="Appointment booking, rescheduling, and cancellation service",
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PATIENT_SERVICE_URL = os.getenv("PATIENT_SERVICE_URL", "http://localhost:8001")
DOCTOR_SERVICE_URL = os.getenv("DOCTOR_SERVICE_URL", "http://localhost:8002")
BILLING_SERVICE_URL = os.getenv("BILLING_SERVICE_URL", "http://localhost:8003")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8007")

# Business rules
MIN_LEAD_TIME_HOURS = 2
MAX_RESCHEDULES = 2
RESCHEDULE_CUTOFF_HOURS = 1
SLOT_DURATION_MINUTES = 30

@app.on_event("startup")
async def startup():
    init_db()

async def verify_patient(patient_id: int) -> bool:
    """Verify patient exists"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{PATIENT_SERVICE_URL}/v1/patients/{patient_id}/exists")
            return response.json().get("exists", False)
        except:
            return False

async def verify_doctor(doctor_id: int, department: Optional[str] = None) -> dict:
    """Verify doctor exists and get department"""
    async with httpx.AsyncClient() as client:
        try:
            if department:
                # Verify department matches
                response = await client.get(f"{DOCTOR_SERVICE_URL}/v1/doctors/{doctor_id}/department")
                dept = response.json().get("department")
                if dept != department:
                    raise HTTPException(status_code=400, detail=f"Doctor does not belong to department {department}")
            else:
                response = await client.get(f"{DOCTOR_SERVICE_URL}/v1/doctors/{doctor_id}")
            
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Doctor not found")
            raise

def validate_slot(slot_start: datetime, slot_end: datetime):
    """Validate slot timing"""
    now = datetime.now()
    
    # Check lead time
    if slot_start < now + timedelta(hours=MIN_LEAD_TIME_HOURS):
        raise HTTPException(
            status_code=400,
            detail=f"Appointment must be at least {MIN_LEAD_TIME_HOURS} hours from now"
        )
    
    # Check clinic hours
    slot_hour = slot_start.hour
    if slot_hour < 9 or slot_hour >= 18:
        raise HTTPException(status_code=400, detail="Appointments must be between 9 AM and 6 PM")
    
    # Check slot duration
    duration = (slot_end - slot_start).total_seconds() / 60
    if duration != SLOT_DURATION_MINUTES:
        raise HTTPException(status_code=400, detail=f"Appointment must be exactly {SLOT_DURATION_MINUTES} minutes")

async def notify_service(event_type: str, data: dict):
    """Send notification to notification service"""
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{NOTIFICATION_SERVICE_URL}/v1/notifications",
                json={"event_type": event_type, "data": data}
            )
        except:
            logger.warning("notification_service_unavailable", event_type=event_type)

@app.post("/v1/appointments", response_model=AppointmentResponse, status_code=201)
async def book_appointment(
    appointment: AppointmentCreate,
    correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """Book a new appointment (idempotent operation)"""
    if not correlation_id:
        correlation_id = str(uuid4())
    
    # Check idempotency - if idempotency key provided, check for existing appointment
    if idempotency_key:
        # In a real system, you'd store idempotency_key in the appointment table
        # For now, we'll use a combination of patient_id, doctor_id, and slot_start as idempotency
        existing = db.query(Appointment).filter(
            and_(
                Appointment.patient_id == appointment.patient_id,
                Appointment.doctor_id == appointment.doctor_id,
                Appointment.slot_start == appointment.slot_start,
                Appointment.status == "SCHEDULED"
            )
        ).first()
        
        if existing:
            logger.info(
                "appointment_already_exists",
                appointment_id=existing.appointment_id,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id
            )
            return existing
    
    # Verify patient exists
    if not await verify_patient(appointment.patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Verify doctor exists and department matches
    doctor = await verify_doctor(appointment.doctor_id, appointment.department)
    
    # Validate slot
    validate_slot(appointment.slot_start, appointment.slot_end)
    
    # Check for overlapping appointments for the same doctor
    overlapping = db.query(Appointment).filter(
        and_(
            Appointment.doctor_id == appointment.doctor_id,
            Appointment.status.in_(["SCHEDULED", "COMPLETED"]),
            Appointment.slot_start < appointment.slot_end,
            Appointment.slot_end > appointment.slot_start
        )
    ).first()
    
    if overlapping:
        raise HTTPException(status_code=409, detail="Doctor has a conflicting appointment")
    
    # Check for patient having overlapping appointments
    patient_overlap = db.query(Appointment).filter(
        and_(
            Appointment.patient_id == appointment.patient_id,
            Appointment.status == "SCHEDULED",
            Appointment.slot_start < appointment.slot_end,
            Appointment.slot_end > appointment.slot_start
        )
    ).first()
    
    if patient_overlap:
        raise HTTPException(status_code=409, detail="Patient has a conflicting appointment")
    
    # Check doctor's daily appointment cap (max 8 appointments/day)
    appointment_date = appointment.slot_start.date()
    doctor_appointments = db.query(Appointment).filter(
        and_(
            Appointment.doctor_id == appointment.doctor_id,
            Appointment.slot_start >= datetime.combine(appointment_date, datetime.min.time()),
            Appointment.slot_start < datetime.combine(appointment_date + timedelta(days=1), datetime.min.time())
        )
    ).count()
    
    if doctor_appointments >= 8:
        raise HTTPException(status_code=400, detail="Doctor has reached maximum daily appointments")
    
    # Create appointment
    db_appointment = Appointment(
        patient_id=appointment.patient_id,
        doctor_id=appointment.doctor_id,
        department=appointment.department,
        slot_start=appointment.slot_start,
        slot_end=appointment.slot_end,
        status="SCHEDULED"
    )
    
    db.add(db_appointment)
    db.commit()
    db.refresh(db_appointment)
    
    logger.info(
        "appointment_created",
        appointment_id=db_appointment.appointment_id,
        patient_id=appointment.patient_id,
        doctor_id=appointment.doctor_id,
        correlation_id=correlation_id
    )
    
    # Send notification
    await notify_service("APPOINTMENT_CONFIRMED", {
        "appointment_id": db_appointment.appointment_id,
        "patient_id": appointment.patient_id,
        "doctor_id": appointment.doctor_id,
        "slot_start": appointment.slot_start.isoformat()
    })
    
    return db_appointment

@app.post("/v1/appointments/{appointment_id}/reschedule")
async def reschedule_appointment(
    appointment_id: int,
    new_slot_start: datetime = Query(...),
    new_slot_end: datetime = Query(...),
    correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
    db: Session = Depends(get_db)
):
    """Reschedule an appointment"""
    if not correlation_id:
        correlation_id = str(uuid4())
    
    appointment = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    if appointment.status != "SCHEDULED":
        raise HTTPException(status_code=400, detail=f"Cannot reschedule {appointment.status} appointment")
    
    # Check reschedule count
    if appointment.reschedule_count >= MAX_RESCHEDULES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_RESCHEDULES} reschedules allowed")
    
    # Check time cutoff
    time_until_slot = (appointment.slot_start - datetime.now()).total_seconds() / 3600
    if time_until_slot <= RESCHEDULE_CUTOFF_HOURS:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reschedule within {RESCHEDULE_CUTOFF_HOURS} hour of appointment"
        )
    
    # Validate new slot
    validate_slot(new_slot_start, new_slot_end)
    
    # Check conflicts
    overlapping = db.query(Appointment).filter(
        and_(
            Appointment.doctor_id == appointment.doctor_id,
            Appointment.appointment_id != appointment_id,
            Appointment.status.in_(["SCHEDULED"]),
            Appointment.slot_start < new_slot_end,
            Appointment.slot_end > new_slot_start
        )
    ).first()
    
    if overlapping:
        raise HTTPException(status_code=409, detail="Doctor has a conflicting appointment at this time")
    
    # Update appointment
    appointment.slot_start = new_slot_start
    appointment.slot_end = new_slot_end
    appointment.reschedule_count = appointment.reschedule_count + 1
    
    db.commit()
    db.refresh(appointment)
    
    logger.info(
        "appointment_rescheduled",
        appointment_id=appointment_id,
        reschedule_count=appointment.reschedule_count,
        correlation_id=correlation_id
    )
    
    await notify_service("APPOINTMENT_RESCHEDULED", {
        "appointment_id": appointment_id,
        "new_slot_start": new_slot_start.isoformat()
    })
    
    return appointment

@app.post("/v1/appointments/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: int,
    correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
    db: Session = Depends(get_db)
):
    """Cancel an appointment"""
    if not correlation_id:
        correlation_id = str(uuid4())
    
    appointment = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    if appointment.status != "SCHEDULED":
        raise HTTPException(status_code=400, detail="Only scheduled appointments can be cancelled")
    
    # Calculate cancellation policy
    now = datetime.now()
    hours_until_slot = (appointment.slot_start - now).total_seconds() / 3600
    
    appointment.status = "CANCELLED"
    db.commit()
    
    logger.info(
        "appointment_cancelled",
        appointment_id=appointment_id,
        hours_until_slot=hours_until_slot,
        correlation_id=correlation_id
    )
    
    # Handle billing
    if hours_until_slot > 2:
        # Full refund
        refund_amount = 0  # No charge initially
    elif hours_until_slot > 0:
        # 50% fee
        # This would be handled by billing service
        pass
    else:
        # No-show fee
        pass
    
    await notify_service("APPOINTMENT_CANCELLED", {
        "appointment_id": appointment_id,
        "refund_info": "Full refund" if hours_until_slot > 2 else "50% refund"
    })
    
    return appointment

@app.post("/v1/appointments/{appointment_id}/complete")
async def complete_appointment(
    appointment_id: int,
    correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
    db: Session = Depends(get_db)
):
    """Mark appointment as completed"""
    if not correlation_id:
        correlation_id = str(uuid4())
    
    appointment = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    if appointment.status != "SCHEDULED":
        raise HTTPException(status_code=400, detail="Only scheduled appointments can be completed")
    
    appointment.status = "COMPLETED"
    db.commit()
    db.refresh(appointment)
    
    logger.info("appointment_completed", appointment_id=appointment_id, correlation_id=correlation_id)
    
    # Create bill
    async with httpx.AsyncClient() as client:
        try:
            bill_response = await client.post(
                f"{BILLING_SERVICE_URL}/v1/bills",
                json={
                    "patient_id": appointment.patient_id,
                    "appointment_id": appointment_id,
                    "amount": 500  # Base consultation fee
                }
            )
            logger.info("bill_created", appointment_id=appointment_id, bill_id=bill_response.json().get("bill_id"))
        except:
            logger.warning("billing_service_unavailable", appointment_id=appointment_id)
    
    await notify_service("APPOINTMENT_COMPLETED", {
        "appointment_id": appointment_id,
        "bill_required": True
    })
    
    return appointment

@app.post("/v1/appointments/{appointment_id}/noshow")
async def mark_no_show(
    appointment_id: int,
    correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
    db: Session = Depends(get_db)
):
    """Mark appointment as no-show"""
    if not correlation_id:
        correlation_id = str(uuid4())
    
    appointment = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    appointment.status = "NO_SHOW"
    db.commit()
    
    logger.info("appointment_noshow", appointment_id=appointment_id, correlation_id=correlation_id)
    
    # Create bill for no-show
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{BILLING_SERVICE_URL}/v1/bills",
                json={
                    "patient_id": appointment.patient_id,
                    "appointment_id": appointment_id,
                    "amount": 250  # 50% no-show fee
                }
            )
        except:
            pass
    
    await notify_service("NO_SHOW", {
        "appointment_id": appointment_id,
        "rebook_link": f"/appointments/book?doctor_id={appointment.doctor_id}"
    })
    
    return appointment

@app.get("/v1/appointments", response_model=List[AppointmentResponse])
def get_appointments(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    patient_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get appointments with filters"""
    query = db.query(Appointment)
    
    if patient_id:
        query = query.filter(Appointment.patient_id == patient_id)
    
    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    
    if status:
        query = query.filter(Appointment.status == status)
    
    total = query.count()
    appointments = query.order_by(Appointment.slot_start.desc()).offset(skip).limit(limit).all()
    
    logger.info("appointments_retrieved", total=total, returned=len(appointments))
    return appointments

@app.get("/v1/appointments/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(appointment_id: int, db: Session = Depends(get_db)):
    """Get appointment by ID"""
    appointment = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    return appointment

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "appointment-service"}

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", 8004))
    uvicorn.run(app, host="0.0.0.0", port=port)

