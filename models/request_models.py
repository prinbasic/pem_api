from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any, List
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

class VerifyOTPtrans(BaseModel):
    phone_number: str = Field(..., example="9876543210")
    otp: str = Field(..., example="123456")
    

class VerifyOtpResponse(BaseModel):
    consent: str
    message: str
    phone_number: Optional[str] = None
    cibilScore: Optional[int] = None
    transId: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    approvedLenders: Optional[List[Dict[str, Any]]] = None
    moreLenders: Optional[List[Dict[str, Any]]] = None
    emi_data: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    # intell_response: Optional[Dict[str, Any]] = None
    user_details: Optional[Dict[str, Any]] = None
    source: str
    emi_data: Optional[List[Dict[str, Any]]] = None
    
    class Config:
        orm_mode = True


