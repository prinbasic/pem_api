from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from typing import Optional, Any, Literal

class LoanFormData(BaseModel):
    name: str
    email: EmailStr
    pan: str
    dob: str
    phone: str
    loanAmount: Optional[int]
    tenureYears: Optional[int] = 20 
    profession: Optional[str] = None 
    location: str
    hascibil: Optional[str] = None
    cibilScore: Optional[int] = None
    proceedScoreCheck: Optional[str]= None
    gender:str
    pin: str
    propertyName: Optional[str] = None

class cibilRequest(BaseModel):
    panNumber: str
    mobileNumber: str
    firstName: str
    lastName: str
    emailAddress: str
    dob: str
    gender: str
    pinCode: str
    applicationId: str
    loanAmount: int
    tenureYears: Optional[int] = 20 
    profession: Optional[str] = None
    propertyName: Optional[str] = None
    hascibil: Optional[str] = None
    cibilScore: Optional[int] = None
    proceedScoreCheck: Optional[str]= None

class cibilOTPRequest(BaseModel):
    transId: str
    otp: str
    pan: str

class PANRequest(BaseModel):
    phone_number: str
    otp: str
    pan_number: str

class PhoneNumberRequest(BaseModel):
    phone_number: str = Field(..., example="917759054070")

class LoanInputRequest(BaseModel):
    panNumber: str
    cibilScore: int
    loanAmount: int
    tenureYears: int
    propertyName: Optional[str] = None
    profession: Optional[str] = None
    propertyType: Optional[str] = None

class SendOTPRequest(BaseModel):
    phone_number: str

class VerifyOTPRequest(BaseModel):
    phone_number: str
    otp: str
    pan_number: Optional[str] = None
    first_name: Optional[str]
    last_name: Optional[str]

class VerifyOtpResponse(BaseModel):
    consent: str
    message: str
    phone_number: Optional[str] = None
    pan_number: Optional[str] = None
    pan_supreme: Optional[dict] = None
    cibil_report: Optional[dict] = None


