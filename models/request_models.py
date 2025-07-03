from pydantic import BaseModel, EmailStr, Field
from typing import Optional

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
    hasCredit: Optional[str] = None
    CreditScore: Optional[int] = None
    proceedScoreCheck: Optional[str]= None
    gender:str
    pin: str
    propertyName: Optional[str] = None


class CreditRequest(BaseModel):
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
    hasCredit: Optional[str] = None
    CreditScore: Optional[int] = None
    proceedScoreCheck: Optional[str]= None
    


class CreditOTPRequest(BaseModel):
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
    CreditScore: int
    loanAmount: int
    tenureYears: int
    propertyName: Optional[str] = None
    profession: Optional[str] = None
    propertyType: Optional[str] = None
