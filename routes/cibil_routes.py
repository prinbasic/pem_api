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
        # If the user opts out of checking the score and doesn't have a CIBIL, return the result immediately
        return result

    # If CIBIL score is available, no further action is needed
    if data.hasCibil == "yes" and data.cibilScore is not None:
        return {"message": "No need for polling, CIBIL score already available."}

    # If there's a transaction ID, proceed with polling
    trans_id = result.get("transId")
    
    if trans_id:
        # If the CIBIL score is not present and a transaction ID exists, start polling
        if not result.get("cibilScore"):
            print(f"🕒 Polling started for transID: {trans_id}")
            
            # Start the polling in a background thread to avoid blocking the main thread
            import threading
            threading.Thread(
                target=poll_consent_and_fetch,
                args=(trans_id, cibil_request.panNumber, cibil_request),
                daemon=True
            ).start()

            # Immediately return the transaction ID while polling continues
            return {
                "status": "polling_started",
                "transId": trans_id
            }

    # If no polling is required, simply return the result
    return result

@router.post("/submit-otp")
def submit_otp(data: CibilOTPRequest):
    return verify_otp_and_fetch_score(data.transId, data.otp, data.pan)
