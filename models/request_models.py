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
    
# class VerifyOtpResponse(BaseModel):
#     consent: str
#     message: str
#     phone_number: Optional[str] = None
#     cibilScore: Optional[int] = None
#     transId: Optional[str] = None
#     raw: Optional[Dict[str, Any]] = None
#     approvedLenders: Optional[List[Dict[str, Any]]] = None
#     moreLenders: Optional[List[Dict[str, Any]]] = None
#     emi_data: Optional[Dict[str, Any]] = None
#     data: Optional[Dict[str, Any]] = None
#     # intell_response: Optional[Dict[str, Any]] = None
#     user_details: Optional[Dict[str, Any]] = None
#     source: str
#     emi_data: float = None
    
#     class Config:
#         orm_mode = True

class VerifyOtpResponse(BaseModel):
    consent: str
    message: str
    phone_number: Optional[str] = None
    cibilScore: Optional[int] = None
    transId: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    approvedLenders: Optional[List[Dict[str, Any]]] = None
    moreLenders: Optional[List[Dict[str, Any]]] = None
    data: Optional[Dict[str, Any]] = None
    user_details: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    emi_data: float = 0.0

    # ✅ pass-through diagnostics from primePan
    flags: Dict[str, bool] = Field(default_factory=dict)
    reason_codes: List[str] = Field(default_factory=list)
    stage: Optional[str] = None

    class Config:
        orm_mode = True


def map_primepan_to_verify_otp(
    *,
    phone_number: str,
    primepan: Dict[str, Any],
    default_source: Optional[str] = None
) -> VerifyOtpResponse:
    """
    Unifies TransBank (CIBIL) and Ongrid (Equifax) payloads into VerifyOtpResponse.
    - Pulls cibilScore from: primepan.cibilScore -> Equifax score_detail -> CIBIL riskScore -> profile_detail.credit_score
    - Pulls transId from: primepan.transId -> cibil_report.transaction_id -> cibil_report.result.transaction_id -> cibil_report.data.transaction_id
    - Chooses source from primepan.source else infers from report body
    """

    cibil_report: Dict[str, Any] = primepan.get("cibil_report") or {}
    profile_detail: Dict[str, Any] = primepan.get("profile_detail") or {}
    data_block = primepan.get("data") or cibil_report.get("data") or {}

    # ----- inline score extraction (no extra helpers) -----
    cibil_score = primepan.get("cibilScore")
    if cibil_score is None:
        # Try Equifax-style: data.profile_data.score_detail[0].value
        try:
            sd = (data_block.get("profile_data", {}) or {}).get("score_detail", []) or []
            if sd:
                v = sd[0].get("value")
                if isinstance(v, (int, float)):
                    cibil_score = int(v)
                elif isinstance(v, str) and v.isdigit():
                    cibil_score = int(v)
        except Exception:
            pass

    if cibil_score is None:
        # Try CIBIL-style: data.cibilData...Borrower.CreditScore.riskScore
        try:
            cibil_score = (
                data_block.get("cibilData", {})
                          .get("GetCustomerAssetsResponse", {})
                          .get("GetCustomerAssetsSuccess", {})
                          .get("Asset", {})
                          .get("TrueLinkCreditReport", {})
                          .get("Borrower", {})
                          .get("CreditScore", {})
                          .get("riskScore")
            )
            if isinstance(cibil_score, str) and cibil_score.isdigit():
                cibil_score = int(cibil_score)
            elif isinstance(cibil_score, float):
                cibil_score = int(cibil_score)
        except Exception:
            pass

    if cibil_score is None:
        # Last resort: profile_detail.credit_score
        cs = profile_detail.get("credit_score")
        if isinstance(cs, (int, float)):
            cibil_score = int(cs)
        elif isinstance(cs, str) and cs.isdigit():
            cibil_score = int(cs)

    # ----- transaction id extraction -----
    trans_id = (
        primepan.get("transId")
        or cibil_report.get("transaction_id")
        or (cibil_report.get("result", {}) or {}).get("transaction_id")
        or (data_block.get("transaction_id"))
    )

    # ----- source inference if missing -----
    source = primepan.get("source") or default_source
    if not source:
        msg = (data_block.get("message") or "").strip()
        html = (data_block.get("htmlUrl") or "").lower()
        if "profile_data" in data_block or msg == "Fetched Bureau Profile.":
            source = "Equifax"
        elif "cibil" in html or isinstance(data_block.get("cibilData"), (dict, bool)) \
             or str(data_block.get("cibilData")).strip().lower() == "cibil":
            source = "cibil"
        else:
            source = default_source

    # ----- message & success -----
    success = bool(primepan.get("success", True))  # be lenient; some happy paths don't set this
    message = primepan.get("message") or ("OTP verified successfully" if success else "Unable to fetch data")

    # ----- emi total -----
    try:
        emi_total = float(primepan.get("emi_data") or 0.0)
    except Exception:
        emi_total = 0.0

    # ----- build the response -----
    return VerifyOtpResponse(
        consent="Y",
        message=message,
        phone_number=phone_number,
        cibilScore=cibil_score if isinstance(cibil_score, (int, type(None))) else None,
        transId=trans_id,
        raw=cibil_report,
        approvedLenders=primepan.get("approvedLenders") or [],
        moreLenders=primepan.get("moreLenders") or [],
        data=data_block,
        user_details=profile_detail,
        source=source,
        emi_data=emi_total,

        # pass-through diagnostics
        flags=primepan.get("flags") or {},
        reason_codes=primepan.get("reason_codes") or [],
        stage=primepan.get("stage"),
    )


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

class updateprofile(BaseModel):
    firstName: str
    lastName: str
    gender: str
    mobile: str
    creditScore: int
    pan: str
    pincode: str
    email: str
    dateOfBirth: str


class mandate_cibil(BaseModel):
    MobileNumber: str
    IsCustomerSelfJourney: bool

class mandate_verify(BaseModel):
    TransId: str
    OTP: str
