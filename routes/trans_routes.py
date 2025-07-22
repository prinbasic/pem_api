from fastapi import APIRouter
from pydantic import BaseModel
from api.cibil_service import send_otp_to_user
from api.trans_service import verify_otp_and_pan
from models.request_models import SendOTPRequest, VerifyOTPRequest

router = APIRouter()


@router.post("/primePan", tags=["transbnk"])
async def verify_otp_and_pan_route(request: VerifyOTPRequest):
    return await verify_otp_and_pan(
        phone_number=request.phone_number,
        otp=request.otp,
    )