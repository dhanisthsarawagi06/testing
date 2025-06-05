from pydantic import BaseModel, EmailStr
from typing import Optional, Dict

class SignUpUserData(BaseModel):
    fullname: str
    email: EmailStr
    mobile: str
    gender: str
    password: str
    confirmpassword: str

class SignInUserData(BaseModel):
    email: EmailStr
    password: str

class GoogleCallbackRequest(BaseModel):
    code: str

class VerifyPhoneRequest(BaseModel):
    phone_number: str
    verification_code: str
    email: Optional[str] = None
    referral_code: Optional[str] = None

class ResendCodeRequest(BaseModel):
    phone_number: str
    email: Optional[str] = None

class CompleteGoogleSignupRequest(BaseModel):
    email: str
    phone_number: str
    google_tokens: Dict

class VerifySellerData(BaseModel):
    panNumber: str
    aadharNumber: str
    upiId: str

class GetUserDetailsRequest(BaseModel):
    email: str

class UpdateUserData(BaseModel):
    name: str

class UpdateUserUpiId(BaseModel):
    upiId: str

class UpdatePasswordData(BaseModel):
    currentPassword: str
    newPassword: str

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str

class UpdatePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str