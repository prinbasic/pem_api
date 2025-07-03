from fastapi import APIRouter, Query, HTTPException, Body
from models.request_models import LoanFormData, cibilRequest
from api.cibil_service import (
    initiate_cibil_score,
    verify_otp_and_fetch_score,
    poll_consent_and_fetch,send_and_verify_pan, send_otp_to_user, resend_otp_to_user, fetch_lenders_and_emi
)
from api.log_utils import log_user_cibil_data
from models.request_models import cibilOTPRequest,PhoneNumberRequest,PANRequest, LoanFormData
import httpx
router = APIRouter()

GRIDLINES_PAN_URL = "https://api.gridlines.io/pan-api/fetch-detailed"
GRIDLINES_API_KEY = "Zvuio2QALeDhyRB0lzZq9o5SgwjBgXcu"
OTP_BASE_URL = "http://3.6.21.243:5000/otp"

# 🟢 New Equifax Flow APIs
@router.post("/initiate-cibil")
def initiate(data: cibilRequest):
    return initiate_cibil_score(data)

@router.get("/verify-otp")
def verify(transId: str = Query(...), otp: str = Query(...), pan: str = Query(...)):
    return verify_otp_and_fetch_score(transId, otp, pan)

@router.get("/poll-consent")
def poll_consent(data: LoanFormData):
    cibil_request = cibilRequest(
        panNumber=data.pan,
        mobileNumber=data.phone,
        firstName=data.name.split(" ")[0],
        lastName=data.name.split(" ")[-1] if " " in data.name else "",
        emailAddress=data.email,
        dob=data.dob,
        gender=data.gender,  # Replace with data.gender if available
        pinCode=data.pin,  # Replace with data.location if it's pincode
        applicationId="BASIC" + data.pan[-4:],
        loanAmount=data.loanAmount,
        propertyName=data.propertyName
    )

    trans_id = "BASIC" + data.pan[-4:]  # OR pass this from frontend
    return poll_consent_and_fetch(trans_id, data.pan, cibil_request)
# 🟡 Original Fallback Endpoints
@router.post("/check-cibil")
def check_cibil(data: LoanFormData):
    return {
        "message": "User does not wish to check cibil score.",
        "name": data.name,
        "note": "Only limited loan options can be shown."
    }

@router.post("/fetch-cibil-score")
def fetch_cibil_score(data: LoanFormData):
    cibil_request = cibilRequest(
        panNumber=data.pan,
        mobileNumber=data.phone,
        firstName=data.name.split(" ")[0],
        lastName=data.name.split(" ")[-1] if " " in data.name else "",
        emailAddress=data.email,
        dob=data.dob,
        gender=data.gender,
        pinCode=data.pin,
        applicationId="BASIC" + data.pan[-4:],
        loanAmount=data.loanAmount,
        propertyName=data.propertyName,
        hascibil=data.hascibil,
        cibilScore=data.cibilScore,
        proceedScoreCheck=data.proceedScoreCheck
    )

    result = initiate_cibil_score(cibil_request)
    if result is True:
        trans_id = result.get("transId")

        if not result.get("cibilScore") and trans_id:
            print(f"🕒 Polling started for transID: {trans_id}")

            # 👇 Let polling run in background (async would be ideal)
            import threading
            threading.Thread(
                target=poll_consent_and_fetch,
                args=(trans_id, cibil_request.panNumber, cibil_request),
                daemon=True
            ).start()

            # ✅ Immediately return transId while polling continues
            return {
                "status": "polling_started",
                "transId": trans_id
            }

    return result

@router.post("/submit-otp")
def submit_otp(data: cibilOTPRequest):
    return verify_otp_and_fetch_score(data.transId, data.otp, data.pan)

@router.post("/consent/send-otp")
def send_otp_route(payload: PhoneNumberRequest):
    return send_otp_to_user(payload.phone_number)

@router.post("/consent/resend-otp")
async def resend_otp_route(payload: PhoneNumberRequest):
    return await resend_otp_to_user(payload.phone_number)

@router.post("/consent/verify-pan")
async def combined_otp_pan_flow(payload: PANRequest):
    return await send_and_verify_pan(
        phone_number=payload.phone_number,
        otp=payload.otp,
        pan_number=payload.pan_number
    )

@router.post("/cibil/fetch-lenders")
async def fetch_lenders_using_score(form: LoanFormData):
    try:
        result = await fetch_lenders_and_emi(form)
        return {
            "message": "Lenders fetched and EMI calculated successfully",
            "result": result
        }
    except Exception as e:
        print("❌ Exception in fetch_lenders_using_score:", e)
        raise HTTPException(status_code=500, detail="Failed to fetch lender and EMI data")