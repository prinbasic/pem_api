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
    emi_data: float = None
    
    class Config:
        orm_mode = True

class PrefillFlags(BaseModel):
    prefill_called: Optional[bool] = False
    prefill_ok: Optional[bool] = False
    no_record_found: Optional[bool] = False
    name_not_found: Optional[bool] = False
    source_unavailable: Optional[bool] = False
    pan_missing: Optional[bool] = False
    parse_error: Optional[bool] = False
    transport_ok: Optional[bool] = None
    prefill_success_101: Optional[bool] = False

    pan_supreme_called: Optional[bool] = False
    pan_supreme_ok: Optional[bool] = False

    cibil_called: Optional[bool] = False
    cibil_ok: Optional[bool] = False
    cibil_error: Optional[bool] = False

    fallback_used: Optional[bool] = False

class TransBankResponse(BaseModel):
    # Legacy fields your FE already consumes
    pan_number: str = ""
    pan_supreme: Dict[str, Any] = Field(default_factory=dict)
    cibil_report: Dict[str, Any] = Field(default_factory=dict)
    profile_detail: Dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    emi_data: float = 0.0

    # New structured metadata
    success: bool
    stage: str
    flags: PrefillFlags = Field(default_factory=PrefillFlags)
    reason_codes: List[str] = Field(default_factory=list)
    message: str = ""
    debug: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"

class IntellReq(BaseModel):
    report: dict   # the JSON object you receive in the body

