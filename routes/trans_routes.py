from fastapi import APIRouter
from pydantic import BaseModel
from api.cibil_service import send_otp_to_user
from api.trans_service import verify_otp_and_pan, trans_bank_fetch_flow
from models.request_models import VerifyOTPtrans, VerifyOtpResponse

router = APIRouter()



@router.post("/primePan", tags=["transbnk"], response_model=VerifyOtpResponse)
async def verify_otp_and_pan_route(request: VerifyOTPtrans) -> VerifyOtpResponse:
    return await verify_otp_and_pan(
        phone_number=request.phone_number,
        otp=request.otp
    ) 