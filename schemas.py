from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from models import AppointmentStatus, Role

T = TypeVar("T")

# Level 4: exactly 10 digits, numeric only (regex enforced by Pydantic)
PHONE_REGEX = r"^[0-9]{10}$"


class AuditFields(BaseModel):
    """Shared by every *Out schema (Level 15)."""

    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class PatchModel(BaseModel):
    """Base for PATCH bodies: fields may be omitted, but not sent as null."""

    @model_validator(mode="after")
    def no_explicit_nulls(self):
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# ---------- Doctors ----------
class DoctorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Dr. Asha Menon"])
    email: EmailStr = Field(examples=["asha.menon@example.com"])
    phone: str = Field(pattern=PHONE_REGEX, examples=["9876543210"])
    specialization: str = Field(min_length=1, max_length=100, examples=["Cardiology"])
    is_active: bool = True

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Dr. Asha Menon",
                    "email": "asha.menon@example.com",
                    "phone": "9876543210",
                    "specialization": "Cardiology",
                    "is_active": True,
                }
            ]
        }
    )


class DoctorUpdate(PatchModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, pattern=PHONE_REGEX)
    specialization: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"specialization": "Neurology", "is_active": False}]}
    )


class DoctorOut(DoctorCreate, AuditFields):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Patients ----------
class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Ravi Kumar"])
    age: int = Field(ge=0, le=130, examples=[40])
    phone: str = Field(pattern=PHONE_REGEX, examples=["9123456780"])
    doctor_id: int = Field(examples=[1])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Ravi Kumar", "age": 40, "phone": "9123456780", "doctor_id": 1}
            ]
        }
    )


class PatientUpdate(PatchModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    age: Optional[int] = Field(default=None, ge=0, le=130)
    phone: Optional[str] = Field(default=None, pattern=PHONE_REGEX)
    doctor_id: Optional[int] = None

    model_config = ConfigDict(json_schema_extra={"examples": [{"age": 41}]})


class PatientOut(PatientCreate, AuditFields):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Appointments ----------
class AppointmentCreate(BaseModel):
    doctor_id: int = Field(examples=[1])
    patient_id: int = Field(examples=[1])
    appointment_date: datetime = Field(examples=["2026-10-05T10:30:00+05:30"])
    status: AppointmentStatus = AppointmentStatus.scheduled

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "doctor_id": 1,
                    "patient_id": 1,
                    "appointment_date": "2026-10-05T10:30:00+05:30",
                    "status": "scheduled",
                }
            ]
        }
    )


class AppointmentUpdate(PatchModel):
    appointment_date: Optional[datetime] = None
    status: Optional[AppointmentStatus] = None

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "completed"}]})


class AppointmentOut(AppointmentCreate, AuditFields):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Pagination ----------
class Page(BaseModel, Generic[T]):
    total: int
    page: int
    limit: int
    data: List[T]


# ---------- Auth ----------
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, examples=["dr_asha"])
    password: str = Field(min_length=6, max_length=128, examples=["secret123"])
    role: Role = Role.doctor
    # Required when role="doctor": links this login to a Doctor row so
    # "view only my patients" (Level 11) knows which doctor "my" means.
    doctor_id: Optional[int] = Field(default=None, examples=[1])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"username": "admin", "password": "secret123", "role": "admin"},
                {
                    "username": "dr_asha",
                    "password": "secret123",
                    "role": "doctor",
                    "doctor_id": 1,
                },
            ]
        }
    )


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: Role
    doctor_id: Optional[int] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
