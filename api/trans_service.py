import httpx
from fastapi import HTTPException

OTP_BASE_URL = "https://dev-api.orbit.basichomeloan.com/api_v1"
MOBILE_TO_PAN_URL = "https://sandbox-api.trusthub.in/mobile-to-pan"
MOBILE_TO_PREFILL_URL = "https://sandbox-api.trusthub.in/mobile-to-prefill-2"
PAN_SUPREME_URL = "https://sandbox-api.trusthub.in/pan-supreme"
CIBIL_URL = "https://sandbox-api.trusthub.in/cibil-report"

API_KEY = "nrqfVLG9zM1Hg66gyV78I53ju91sEHEpcawO9Cs6"
HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json; charset=utf-8"
}

async def verify_otp_and_pan(phone_number: str, otp: str, pan_number: str = None):
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Step 1: OTP Verification
            verify_response = await client.post(
                f"{OTP_BASE_URL}/otp_verify",
                json={"phone_number": phone_number, "otp": otp}
            )
            verify_data = verify_response.json()
            print(f"✅ OTP Verify Response [{verify_response.status_code}]: {verify_data}")

            if verify_response.status_code != 200 or not verify_data.get("success"):
                return {"consent": "N", "message": "OTP verification failed"}

            # Step 2: Fetch PAN and CIBIL Flow
            fetch_data = await trans_bank_fetch_flow(
                phone_number=phone_number if not pan_number else None,
                pan_number=pan_number
            )

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

# async def trans_bank_fetch_flow(phone_number: str = None, pan_number: str = None):
#     async with httpx.AsyncClient(timeout=60.0) as client:
#         if pan_number:
#             print("📌 Using provided PAN number directly.")
#             final_pan_number = pan_number

#         elif phone_number:
#             print("📌 Fetching PAN from Mobile Number using Mobile to Prefill API with name_lookup: 0.")
#             static_first_name = "PRINCE"
#             static_last_name = "RAJ"

#             mobile_to_prefill_payload = {
#                 "client_ref_num": "BBA225PR001",  # Ideally generate dynamically
#                 "mobile_no": phone_number,
#                 "name_lookup": 0,
#                 "first_name": static_first_name,
#                 "last_name": static_last_name,
#                 "name_fallback": 1
#             }

#             mobile_to_prefill_resp = await client.post(
#                 MOBILE_TO_PREFILL_URL,
#                 headers=HEADERS,
#                 json=mobile_to_prefill_payload
#             )

#             print(f"🔍 Mobile to Prefill API Response Status [{mobile_to_prefill_resp.status_code}]")
#             mobile_to_prefill_data = mobile_to_prefill_resp.json()
#             print(f"🔍 Mobile to Prefill API Response Data: {mobile_to_prefill_data}")

#             if mobile_to_prefill_resp.status_code != 200:
#                 raise HTTPException(
#                     status_code=mobile_to_prefill_resp.status_code,
#                     detail=f"Mobile to Prefill API call failed: {mobile_to_prefill_data.get('message', 'No message')}"
#                 )

#             if not mobile_to_prefill_data.get("success"):
#                 raise HTTPException(
#                     status_code=400,
#                     detail=f"Mobile to Prefill fetch failed: {mobile_to_prefill_data.get('message', 'No message')}"
#                 )

#             final_pan_number = mobile_to_prefill_data["data"]["pan_number"]
#             print(f"✅ PAN fetched from mobile via Prefill API: {final_pan_number}")

#         else:
#             raise HTTPException(status_code=400, detail="Either PAN number or Phone number must be provided")

#         # Step 2: PAN Supreme API
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

#         # Step 3: CIBIL Report
#         cibil_payload = {
#             "CustomerInfo": {
#                 "Name": {
#                     "Forename": pan_details["first_name"],
#                     "Surname": pan_details["last_name"]
#                 },
#                 "IdentificationNumber": {
#                     "IdentifierName": "TaxId",
#                     "Id": final_pan_number
#                 },
#                 "Address": {
#                     "StreetAddress": pan_details["address"]["address_line_1"],
#                     "City": pan_details["address"]["state"],
#                     "PostalCode": pan_details["address"]["pin_code"],
#                     "Region": 27,
#                     "AddressType": 1
#                 },
#                 "EmailID": pan_details.get("email") or "",
#                 "DateOfBirth": pan_details["dob"],
#                 "PhoneNumber": {"Number": phone_number if phone_number else ""},
#                 "Gender": pan_details["gender"]
#             },
#             "LegalCopyStatus": "Accept",
#             "UserConsentForDataSharing": True
#         }

#         cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)
#         cibil_data = cibil_resp.json()
#         print(f"✅ CIBIL Report Data: {cibil_data}")

#         return {
#             "pan_number": final_pan_number,
#             "pan_supreme": pan_supreme_data,
#             "cibil_report": cibil_data
#         }


async def trans_bank_fetch_flow(phone_number: str = None, pan_number: str = None):
    async with httpx.AsyncClient(timeout=60.0) as client:
        if pan_number:
            print("📌 Using provided PAN number directly.")
            final_pan_number = pan_number

        elif phone_number:
            print("📌 Fetching PAN from Mobile Number using Mobile to Prefill API with name_lookup: 0.")
            static_first_name = "PRINCE"
            static_last_name = "RAJ"

            mobile_to_prefill_payload = {
                "client_ref_num": "BBA225PR001",  # Ideally generate dynamically
                "mobile_no": phone_number,
                "name_lookup": 0,
                "first_name": static_first_name,
                "last_name": static_last_name,
                "name_fallback": 1
            }

            mobile_to_prefill_resp = await client.post(
                MOBILE_TO_PREFILL_URL,
                headers=HEADERS,
                json=mobile_to_prefill_payload
            )

            print(f"🔍 Mobile to Prefill API Response Status [{mobile_to_prefill_resp.status_code}]")
            mobile_to_prefill_data = mobile_to_prefill_resp.json()
            print("📋 Full Mobile to Prefill API Response:")
            print(mobile_to_prefill_data)

            # Proceeding without blocking on success check for now
            final_pan_number = None
            if mobile_to_prefill_data.get("result"):
                final_pan_number = mobile_to_prefill_data["result"].get("pan")
                print(f"✅ Extracted PAN number: {final_pan_number}")
            else:
                raise HTTPException(
                    status_code=mobile_to_prefill_resp.status_code,
                    detail="Mobile to Prefill API call failed."
                )

            if not final_pan_number:
                raise HTTPException(status_code=400, detail="PAN number not returned in Mobile to Prefill response.")

        else:
            raise HTTPException(status_code=400, detail="Either PAN number or Phone number must be provided")

        # Step 2: PAN Supreme API
        pan_supreme_resp = await client.post(
            PAN_SUPREME_URL,
            headers=HEADERS,
            json={"pan": final_pan_number}
        )
        pan_supreme_data = pan_supreme_resp.json()
        print(f"🔍 PAN Supreme API Response: {pan_supreme_data}")

        if pan_supreme_data.get("status") != "1":
            raise HTTPException(
                status_code=400,
                detail=f"PAN Supreme verification failed: {pan_supreme_data.get('message', 'No message')}"
            )

        pan_details = pan_supreme_data["result"]
        print(f"✅ PAN Supreme Details: {pan_details}")

        # Step 3: CIBIL Report
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
                "PhoneNumber": {"Number": phone_number if phone_number else ""},
                "Gender": pan_details["gender"]
            },
            "LegalCopyStatus": "Accept",
            "UserConsentForDataSharing": True
        }
        print(f"cibil report payload:{cibil_payload}")
        cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)
        cibil_data = cibil_resp.json()
        print(f"✅ CIBIL Report Data: {cibil_data}")

        return {
            "pan_number": final_pan_number,
            "pan_supreme": pan_supreme_data,
            "cibil_report": cibil_data
        }


