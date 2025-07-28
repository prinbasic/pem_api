from fastapi import HTTPException
from datetime import datetime
import random
import string
import httpx
OTP_BASE_URL = "https://dev-api.orbit.basichomeloan.com/api_v1"
MOBILE_TO_PAN_URL = "https://sandbox-api.trusthub.in/mobile-to-pan"
MOBILE_TO_PREFILL_URL = "https://sandbox-api.trusthub.in/mobile-to-prefill-2"
# PAN_SUPREME_URL = "https://sandbox-api.trusthub.in/pan-supreme"
PAN_SUPREME_URL = "https://api.trusthub.in/pan-supreme"
CIBIL_URL = "https://sandbox-api.trusthub.in/cibil-report"

API_KEY = "nrqfVLG9zM1Hg66gyV78I53ju91sEHEpcawO9Cs6"
HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json; charset=utf-8"
}

def generate_ref_num(prefix="BBA"):
    timestamp = datetime.now().strftime("%d%m%y")
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
    return f"{prefix}{timestamp}{suffix}"

# async def verify_otp_and_pan(phone_number: str, otp: str):
#     async with httpx.AsyncClient(timeout=60.0) as client:
#         try:
#             # Step 1: OTP Verification
#             verify_response = await client.post(
#                 f"{OTP_BASE_URL}/otp_verify",
#                 json={"phone_number": phone_number, "otp": otp}
#             )
#             verify_data = verify_response.json()
#             print(f"✅ OTP Verify Response [{verify_response.status_code}]: {verify_data}")

#             if verify_response.status_code != 200 or not verify_data.get("success"):
#                 return {"consent": "N", "message": "OTP verification failed"}

#             # Step 2: Fetch PAN and CIBIL Flow
#             fetch_data = await trans_bank_fetch_flow(
#                 phone_number=phone_number ,
#             )

#             return {
#                 "consent": "Y",
#                 "message": "OTP verified and data fetched successfully",
#                 "phone_number": phone_number,
#                 "pan_number": fetch_data.get("pan_number"),
#                 "pan_supreme": fetch_data.get("pan_supreme"),
#                 "cibil_report": fetch_data.get("cibil_report")
#             }

#         except Exception as e:
#             raise HTTPException(status_code=500, detail=f"Verification and data fetch failed: {str(e)}")

# async def trans_bank_fetch_flow(phone_number: str = None, pan_number: str = None, first_name: str = None, last_name: str = None):
#     async with httpx.AsyncClient(timeout=60.0) as client:
#         if pan_number:
#             print("📌 Using provided PAN number directly.")
#             final_pan_number = pan_number

#         elif phone_number:
#             print("📌 Fetching PAN from Mobile Number using Mobile to Prefill API with name_lookup: 0.")
#             client_ref_num = generate_ref_num()

#             mobile_to_prefill_payload = {
#                 "client_ref_num": client_ref_num,
#                 "mobile_no": phone_number,
#                 "name_lookup": 0,
#                 "first_name": first_name,
#                 "last_name": last_name,
#                 "name_fallback": 1
#             }

#             mobile_to_prefill_resp = await client.post(
#                 MOBILE_TO_PREFILL_URL,
#                 headers=HEADERS,
#                 json=mobile_to_prefill_payload
#             )

#             print(f"🔍 Mobile to Prefill API Response Status [{mobile_to_prefill_resp.status_code}]")
#             mobile_to_prefill_data = mobile_to_prefill_resp.json()
#             print("📋 Full Mobile to Prefill API Response:")
#             print(mobile_to_prefill_data)

#             final_pan_number = None
#             if mobile_to_prefill_data.get("result"):
#                 final_pan_number = mobile_to_prefill_data["result"].get("pan")
#                 print(f"✅ Extracted PAN number: {final_pan_number}")
#             else:
#                 raise HTTPException(
#                     status_code=mobile_to_prefill_resp.status_code,
#                     detail="Mobile to Prefill API call failed."
#                 )

#             if not final_pan_number:
#                 raise HTTPException(status_code=400, detail="PAN number not returned in Mobile to Prefill response.")

#         else:
#             raise HTTPException(status_code=400, detail="Either PAN number or Phone number must be provided")

#         # PAN Supreme API
#         pan_supreme_resp = await client.post(
#             PAN_SUPREME_URL,
#             headers=HEADERS,
#             json={"pan": final_pan_number}
#         )
#         pan_supreme_data = pan_supreme_resp.json()
#         print(f"🔍 PAN Supreme API Response: {pan_supreme_data}")

#         if pan_supreme_data.get("status") != "1":
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"PAN Supreme verification failed: {pan_supreme_data.get('message', 'No message')}"
#             )

#         pan_details = pan_supreme_data["result"]
#         print(f"✅ PAN Supreme Details: {pan_details}")

#         # CIBIL Report
#         try:
#             cibil_payload = {
#                 "CustomerInfo": {
#                     "Name": {
#                         "Forename": pan_details["first_name"],
#                         "Surname": pan_details["last_name"]
#                     },
#                     "IdentificationNumber": {
#                         "IdentifierName": "TaxId",
#                         "Id": final_pan_number
#                     },
#                     "Address": {
#                         "StreetAddress": pan_details["address"]["address_line_1"],
#                         "City": pan_details["address"]["state"],
#                         "PostalCode": pan_details["address"]["pin_code"],
#                         "Region": 20,
#                         "AddressType": 1
#                     },
#                     "EmailID": pan_details.get("email") or "",
#                     "DateOfBirth": pan_details["dob"],
#                     "PhoneNumber": {"Number": phone_number if phone_number else ""},
#                     "Gender": pan_details["gender"]
#                 },
#                 "LegalCopyStatus": "Accept",
#                 "UserConsentForDataSharing": True
#             }
#         except Exception as e:
#             raise HTTPException(status_code=500, detail="CIBIL payload creation failed")

#         cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)
#         cibil_data = cibil_resp.json()

#         return {
#             "pan_number": final_pan_number,
#             "pan_supreme": pan_supreme_data,
#             "cibil_report": cibil_data
#         }

async def trans_bank_fetch_flow(phone_number: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        if not phone_number:
            raise HTTPException(status_code=400, detail="Phone number is required")

        print("📌 Fetching PAN using Mobile to Prefill API with name_lookup: 1.")
        client_ref_num = generate_ref_num()

        mobile_to_prefill_payload = {
            "client_ref_num": client_ref_num,
            "mobile_no": phone_number,
            "name_lookup": 1
        }

        mobile_to_prefill_resp = await client.post(
            MOBILE_TO_PREFILL_URL,
            headers=HEADERS,
            json=mobile_to_prefill_payload
        )

        print(f"🔍 Mobile to Prefill API Response Status [{mobile_to_prefill_resp.status_code}]")
        mobile_to_prefill_data = mobile_to_prefill_resp.json()
        print("📋 Full Mobile to Prefill API Response:", mobile_to_prefill_data)

        if not mobile_to_prefill_data.get("result") or not mobile_to_prefill_data["result"].get("pan"):
            raise HTTPException(
                status_code=400,
                detail="PAN number not returned in Mobile to Prefill response."
            )

        final_pan_number = mobile_to_prefill_data["result"]["pan"]
        print(f"✅ Extracted PAN number: {final_pan_number}")

        # PAN Supreme
        pan_supreme_resp = await client.post(
            PAN_SUPREME_URL,
            headers=HEADERS,
            json={"pan": final_pan_number}
        )
        pan_supreme_data = pan_supreme_resp.json()
        print(f"🔍 PAN Supreme API Response: {pan_supreme_data}")

        if pan_supreme_data.get("status") != "1":
            print(final_pan_number)
            raise HTTPException(
                status_code=400,
                detail=f"PAN Supreme verification failed: {pan_supreme_data.get('message', 'No message')}"
            )

        pan_details = pan_supreme_data["result"]
        print(f"✅ PAN Supreme Details: {pan_details}")

        # CIBIL Payload
        try:
            cibil_payload = {
                "CustomerInfo": {
                    "Name": {
                        "Forename": pan_details["first_name"],
                        "Surname": pan_details["last_name"]
                    },
                    "IdentificationNumber": {
                        "IdentifierName": "TaxId",
                        "Id": final_pan_number
                    },
                    "Address": {
                        "StreetAddress": pan_details["address"]["address_line_1"],
                        "City": pan_details["address"]["state"],
                        "PostalCode": pan_details["address"]["pin_code"],
                        "Region": 20,
                        "AddressType": 1
                    },
                    "EmailID": pan_details.get("email") or "",
                    "DateOfBirth": pan_details["dob"],
                    "PhoneNumber": {"Number": phone_number},
                    "Gender": pan_details["gender"]
                },
                "LegalCopyStatus": "Accept",
                "UserConsentForDataSharing": True
            }
        except Exception:
            raise HTTPException(status_code=500, detail="CIBIL payload creation failed")

        print(cibil_payload)
        cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)
        cibil_data = cibil_resp.json()

        return {
            "pan_number": final_pan_number,
            "pan_supreme": pan_supreme_data,
            "cibil_report": cibil_data
        }

async def verify_otp_and_pan(phone_number: str, otp: str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            verify_response = await client.post(
                f"{OTP_BASE_URL}/otp_verify",
                json={"phone_number": phone_number, "otp": otp}
            )
            verify_data = verify_response.json()
            print(f"✅ OTP Verify Response [{verify_response.status_code}]: {verify_data}")

            if verify_response.status_code != 200 or not verify_data.get("success"):
                return {"consent": "N", "message": "OTP verification failed"}

            fetch_data = await trans_bank_fetch_flow(phone_number=phone_number)

            return {
                "consent": "Y",
                "message": "OTP verified and data fetched successfully",
                "phone_number": phone_number,
                "pan_number": fetch_data.get("pan_number"),
                "pan_supreme": fetch_data.get("pan_supreme"),
                "cibil_report": fetch_data.get("cibil_report")
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Verification and data fetch failed: {str(e)}")

