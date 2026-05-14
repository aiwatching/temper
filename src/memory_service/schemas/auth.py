"""Request/response models for the auth endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    # Accept either a full email (matched against users.email) or a
    # short handle (matched against users.username). EmailStr would
    # reject "admin" outright; we hand-validate further down.
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    username: str | None = None
    display_name: str | None
    org_id: str | None
    is_super_admin: bool
    is_org_admin: bool
    is_active: bool = True
    must_change_password: bool = False
    created_at: datetime


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class InitialAdminRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)
