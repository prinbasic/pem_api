from fastapi import APIRouter, Query
from models.request_models import LoanFormData, CibilRequest
from api.cibil_service import (
    initiate_cibil_score,
    verify_otp_and_fetch_score,
    poll_consent_and_fetch,
)
from api.log_utils import log_user_cibil_data
from models.request_models import CibilOTPRequest

router = APIRouter()

# 🟢 New Equifax Flow APIs
@router.post("/initiate-cibil")
def initiate(data: CibilRequest):
    return initiate_cibil_score(data)

@router.get("/verify-otp")
def verify(transId: str = Query(...), otp: str = Query(...), pan: str = Query(...)):
    return verify_otp_and_fetch_score(transId, otp, pan)

@router.get("/poll-consent")
def poll_consent(data: LoanFormData):
    cibil_request = CibilRequest(
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
        "message": "User does not wish to check CIBIL score.",
        "name": data.name,
        "note": "Only limited loan options can be shown."
    }

@router.post("/fetch-cibil-score")
def fetch_cibil_score(data: LoanFormData):
    cibil_request = CibilRequest(
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
        hasCibil=data.hasCibil,
        cibilScore=data.cibilScore,
        proceedScoreCheck=data.proceedScoreCheck
    )

    result = initiate_cibil_score(cibil_request)
    if data.proceedScoreCheck == "no" and data.hasCibil == "no":
        return result
    else:
        if data.hasCibil == "yes" and result.get("cibilScore"):
            return {"no need of polling"}
        else:
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
def submit_otp(data: CibilOTPRequest):
    return verify_otp_and_fetch_score(data.transId, data.otp, data.pan)
