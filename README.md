# Appointment Service

Microservice for managing appointments in the Hospital Management System.

## Overview

The Appointment Service handles booking, rescheduling, cancellation, and completion of appointments with complex business rules and inter-service communication.

## Features

- ✅ Book appointments with full validation
- ✅ Reschedule with business rules (max 2 reschedules)
- ✅ Cancel appointments with refund policy
- ✅ Complete appointments → auto-create bills
- ✅ No-show handling
- ✅ Inter-service communication (Patient, Doctor, Billing, Notification)
- ✅ API version `/v1`
- ✅ OpenAPI 3.0 documentation

## Business Rules

- Minimum 2-hour lead time for bookings
- Clinic hours: 9 AM to 6 PM
- Maximum 2 reschedules per appointment
- Cannot reschedule within 1 hour of appointment
- Cancellation policy: >2h (full refund), ≤2h (50% fee), no-show (100% fee)
- Maximum 1 active appointment per patient per time slot
- Maximum 8 appointments/day per doctor
- Department mismatch validation

## Quick Start

### Prerequisites

- Python 3.8+
- pip
- Other services running (Patient, Doctor, Billing, Notification)

### Installation

```bash
pip install -r requirements.txt
```

### Running Locally

```bash
python app.py
```

The service will start on `http://localhost:8004`

### Environment Variables

- `PORT` - Service port (default: 8004)
- `DATABASE_URL` - Database connection string (default: sqlite:///./appointment.db)
- `PATIENT_SERVICE_URL` - Patient service URL (default: http://localhost:8001)
- `DOCTOR_SERVICE_URL` - Doctor service URL (default: http://localhost:8002)
- `BILLING_SERVICE_URL` - Billing service URL (default: http://localhost:8003)
- `NOTIFICATION_SERVICE_URL` - Notification service URL (default: http://localhost:8007)

### Using Docker

```bash
docker build -t appointment-service:latest .
docker run -p 8004:8004 appointment-service:latest
```

## API Documentation

Once the service is running, visit:
- Swagger UI: http://localhost:8004/docs
- ReDoc: http://localhost:8004/redoc

## Endpoints

- `POST /v1/appointments` - Book a new appointment
- `GET /v1/appointments` - List appointments (with filters)
- `GET /v1/appointments/{appointment_id}` - Get appointment by ID
- `POST /v1/appointments/{appointment_id}/reschedule` - Reschedule appointment
- `POST /v1/appointments/{appointment_id}/cancel` - Cancel appointment
- `POST /v1/appointments/{appointment_id}/complete` - Complete appointment
- `POST /v1/appointments/{appointment_id}/noshow` - Mark as no-show
- `GET /health` - Health check endpoint

## Kubernetes Deployment

```bash
kubectl apply -f k8s/deployment.yaml
```

## Database Schema

**Appointments Table:**
- `appointment_id` (Integer, Primary Key)
- `patient_id` (Integer, Foreign Key)
- `doctor_id` (Integer, Foreign Key)
- `department` (String)
- `slot_start` (DateTime)
- `slot_end` (DateTime)
- `status` (String: SCHEDULED, COMPLETED, CANCELLED, NO_SHOW)
- `reschedule_count` (Integer)
- `created_at` (DateTime)

## Inter-Service Communication

This service communicates with:
- **Patient Service**: Verify patient exists
- **Doctor Service**: Verify doctor exists and department matches
- **Billing Service**: Create bills on completion/cancellation
- **Notification Service**: Send notifications for all events

## Contributing

This is part of a microservices architecture. For integration with other services, see the main Hospital Management System documentation.

## License

Academic use only.

