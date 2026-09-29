"""Business logic. Routes stay thin and only call these functions."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from models import Appointment, AppointmentStatus, Doctor, Patient, Role, User
from schemas import AppointmentCreate, DoctorCreate, PatientCreate
from security import hash_password, verify_password

logger = logging.getLogger("hospital_api.services")

# Level 12: how long one appointment "occupies" a doctor, for the overlap check
APPOINTMENT_DURATION = timedelta(minutes=30)


class ServiceError(Exception):
    """Business-rule failure; main.py turns it into a structured JSON error."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code, self.code, self.message = status_code, code, message


# ---------- helpers ----------
def paginate(db: Session, stmt, page: int, limit: int):
    """COUNT in SQL, then LIMIT/OFFSET in SQL: never loads the whole table."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    items = db.scalars(stmt.offset((page - 1) * limit).limit(limit)).all()
    return total, items


def commit(db: Session):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Level 13: translate raw DB errors into a message a client can act on,
        # instead of leaking driver/SQL text
        message = "A database constraint was violated"
        text = str(exc.orig).lower()
        if "unique" in text and "email" in text:
            message = "A doctor with this email already exists"
        elif "foreign key" in text:
            # SQLite doesn't distinguish "parent missing" from "still referenced
            # by a child row" in its error text, so cover both readings
            message = "This record is linked to another (e.g. still has appointments) or references one that doesn't exist"
        raise ServiceError(400, "integrity_error", message)


def stamp_create(obj, username: str):
    obj.created_by = username
    obj.updated_by = username


def stamp_update(obj, username: str):
    obj.updated_by = username


# ---------- doctors ----------
def get_doctor_or_404(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise ServiceError(404, "doctor_not_found", f"Doctor {doctor_id} does not exist")
    return doctor


def require_active_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = get_doctor_or_404(db, doctor_id)
    if not doctor.is_active:
        raise ServiceError(400, "doctor_inactive", f"Doctor {doctor_id} is inactive")
    return doctor


def ensure_email_unique(db: Session, email: str, exclude_id: Optional[int] = None):
    stmt = select(Doctor.id).where(func.lower(Doctor.email) == email.lower())
    if exclude_id is not None:
        stmt = stmt.where(Doctor.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise ServiceError(400, "duplicate_email", "A doctor with this email already exists")


def create_doctor(db: Session, data: DoctorCreate, actor: str) -> Doctor:
    ensure_email_unique(db, data.email)
    values = data.model_dump()
    values["email"] = values["email"].lower()
    doctor = Doctor(**values)
    stamp_create(doctor, actor)
    db.add(doctor)
    commit(db)
    db.refresh(doctor)
    logger.info("Created doctor id=%s", doctor.id)
    return doctor


def list_doctors(db, specialization, is_active, page, limit):
    stmt = select(Doctor).order_by(Doctor.id)
    if specialization is not None:
        stmt = stmt.where(func.lower(Doctor.specialization) == specialization.lower())
    if is_active is not None:
        stmt = stmt.where(Doctor.is_active == is_active)
    return paginate(db, stmt, page, limit)


def update_doctor(db: Session, doctor_id: int, values: dict, actor: str) -> Doctor:
    """Used by both PUT (all fields) and PATCH (only sent fields)."""
    doctor = get_doctor_or_404(db, doctor_id)
    if "email" in values:
        values["email"] = values["email"].lower()
        ensure_email_unique(db, values["email"], exclude_id=doctor_id)
    for key, value in values.items():
        setattr(doctor, key, value)
    stamp_update(doctor, actor)
    commit(db)
    db.refresh(doctor)
    return doctor


def soft_delete_doctor(db: Session, doctor_id: int, actor: str) -> None:
    doctor = get_doctor_or_404(db, doctor_id)
    doctor.is_active = False  # never remove the row
    stamp_update(doctor, actor)
    commit(db)
    logger.info("Soft-deleted doctor id=%s", doctor_id)


def list_doctor_patients(db: Session, doctor_id: int, page: int, limit: int):
    require_active_doctor(db, doctor_id)  # 404 if missing, 400 if inactive
    stmt = select(Patient).where(Patient.doctor_id == doctor_id).order_by(Patient.id)
    return paginate(db, stmt, page, limit)


# ---------- patients ----------
def get_patient_or_404(db: Session, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise ServiceError(404, "patient_not_found", f"Patient {patient_id} does not exist")
    return patient


def create_patient(db: Session, data: PatientCreate, actor: str) -> Patient:
    require_active_doctor(db, data.doctor_id)
    patient = Patient(**data.model_dump())
    stamp_create(patient, actor)
    db.add(patient)
    commit(db)
    db.refresh(patient)
    logger.info("Created patient id=%s doctor_id=%s", patient.id, patient.doctor_id)
    return patient


def list_patients(db, age_gt, doctor_id, page, limit, current_user: User):
    # Level 11: a doctor-role user only ever sees their own patients,
    # regardless of what doctor_id they pass in
    stmt = select(Patient).order_by(Patient.id)
    if current_user.role == Role.doctor:
        stmt = stmt.where(Patient.doctor_id == current_user.doctor_id)
    elif doctor_id is not None:
        stmt = stmt.where(Patient.doctor_id == doctor_id)
    if age_gt is not None:
        stmt = stmt.where(Patient.age > age_gt)
    return paginate(db, stmt, page, limit)


def update_patient(db: Session, patient_id: int, values: dict, actor: str) -> Patient:
    patient = get_patient_or_404(db, patient_id)
    if "doctor_id" in values:
        require_active_doctor(db, values["doctor_id"])
    for key, value in values.items():
        setattr(patient, key, value)
    stamp_update(patient, actor)
    commit(db)
    db.refresh(patient)
    return patient


def delete_patient(db: Session, patient_id: int) -> None:
    patient = get_patient_or_404(db, patient_id)
    db.delete(patient)
    commit(db)
    logger.info("Deleted patient id=%s", patient_id)


def assert_can_view_patient(patient: Patient, current_user: User):
    if current_user.role == Role.doctor and patient.doctor_id != current_user.doctor_id:
        raise ServiceError(403, "forbidden", "Not authorized to view this patient")


# ---------- appointments ----------
def get_appointment_or_404(db: Session, appointment_id: int) -> Appointment:
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise ServiceError(404, "appointment_not_found", f"Appointment {appointment_id} does not exist")
    return appt


def assert_can_act_on_doctor(current_user: User, doctor_id: int):
    if current_user.role == Role.doctor and current_user.doctor_id != doctor_id:
        raise ServiceError(403, "forbidden", "Not authorized for this doctor's appointments")


def check_no_overlap(db: Session, doctor_id: int, start: datetime, exclude_id: Optional[int] = None):
    """Level 12: two appointments for the same doctor may not occupy overlapping
    30-minute slots. Cancelled appointments don't block a slot.

    Interval math is done in Python (not `column + timedelta` in SQL) because
    SQLite can't compile datetime-column arithmetic; two plain comparisons
    against precomputed bounds work on every backend.
    """
    new_end = start + APPOINTMENT_DURATION
    earliest_conflicting_start = start - APPOINTMENT_DURATION
    stmt = select(Appointment.id).where(
        Appointment.doctor_id == doctor_id,
        Appointment.status != AppointmentStatus.cancelled,
        Appointment.appointment_date < new_end,
        Appointment.appointment_date > earliest_conflicting_start,
    )
    if exclude_id is not None:
        stmt = stmt.where(Appointment.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise ServiceError(400, "appointment_overlap", "Doctor already has an appointment in this time slot")


def create_appointment(db: Session, data: AppointmentCreate, actor: str) -> Appointment:
    require_active_doctor(db, data.doctor_id)
    get_patient_or_404(db, data.patient_id)
    check_no_overlap(db, data.doctor_id, data.appointment_date)
    appt = Appointment(**data.model_dump())
    stamp_create(appt, actor)
    db.add(appt)
    commit(db)
    db.refresh(appt)
    logger.info("Created appointment id=%s doctor_id=%s", appt.id, appt.doctor_id)
    return appt


def update_appointment(db: Session, appointment_id: int, values: dict, actor: str) -> Appointment:
    appt = get_appointment_or_404(db, appointment_id)
    new_doctor_id = values.get("doctor_id", appt.doctor_id)
    new_date = values.get("appointment_date", appt.appointment_date)
    if "doctor_id" in values or "appointment_date" in values:
        check_no_overlap(db, new_doctor_id, new_date, exclude_id=appt.id)
    for key, value in values.items():
        setattr(appt, key, value)
    stamp_update(appt, actor)
    commit(db)
    db.refresh(appt)
    return appt


def delete_appointment(db: Session, appointment_id: int) -> None:
    appt = get_appointment_or_404(db, appointment_id)
    db.delete(appt)
    commit(db)


def list_doctor_appointments(db: Session, doctor_id: int, page: int, limit: int):
    get_doctor_or_404(db, doctor_id)
    # Level 14: selectinload avoids a second query per row (N+1) if the
    # caller ever touches appt.patient / appt.doctor while serializing
    stmt = (
        select(Appointment)
        .where(Appointment.doctor_id == doctor_id)
        .options(selectinload(Appointment.patient), selectinload(Appointment.doctor))
        .order_by(Appointment.appointment_date)
    )
    return paginate(db, stmt, page, limit)


def list_patient_appointments(db: Session, patient_id: int, page: int, limit: int):
    get_patient_or_404(db, patient_id)
    stmt = (
        select(Appointment)
        .where(Appointment.patient_id == patient_id)
        .options(selectinload(Appointment.doctor), selectinload(Appointment.patient))
        .order_by(Appointment.appointment_date)
    )
    return paginate(db, stmt, page, limit)


# ---------- users / auth ----------
def register_user(db: Session, data) -> User:
    if db.scalar(select(User).where(User.username == data.username)):
        raise ServiceError(400, "username_taken", "Username already registered")
    if data.role == Role.doctor and data.doctor_id is not None:
        require_active_doctor(db, data.doctor_id)
    user = User(username=data.username, hashed_password=hash_password(data.password), role=data.role)
    db.add(user)
    commit(db)
    db.refresh(user)
    if data.role == Role.doctor and data.doctor_id is not None:
        doctor = get_doctor_or_404(db, data.doctor_id)
        doctor.user_id = user.id
        commit(db)
        db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.hashed_password):
        raise ServiceError(401, "invalid_credentials", "Incorrect username or password")
    return user
