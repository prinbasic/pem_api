from fastapi import HTTPException
from datetime import datetime
import random
import string
import httpx
import tempfile
import json
import requests
from db_client import get_db_connection  # make sure this is imported
from datetime import datetime, timezone
OTP_BASE_URL = "https://dev-api.orbit.basichomeloan.com/api_v1"
MOBILE_TO_PAN_URL = "https://sandbox-api.trusthub.in/mobile-to-pan"
MOBILE_TO_PREFILL_URL = "https://sandbox-api.trusthub.in/mobile-to-prefill-2"
PAN_SUPREME_URL = "https://sandbox-api.trusthub.in/pan-supreme"
# PAN_SUPREME_URL = "https://api.trusthub.in/pan-supreme"
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
                        "Forename": pan_details.get("first_name", "").strip(),
                        "Surname": pan_details.get("last_name", "").strip()
                    },
                    "IdentificationNumber": {
                        "IdentifierName": "TaxId",
                        "Id": final_pan_number.strip()
                    },
                    "Address": {
                        "StreetAddress": pan_details["address"].get("address_line_1", "").strip(),
                        "City": pan_details["address"].get("address_line_5", "").strip(),  # BOKARO
                        "PostalCode": int(pan_details["address"].get("pin_code", 0)),
                        "Region": 20,
                        "AddressType": 1
                    },
                    "EmailID": pan_details.get("email", "").strip(),
                    "DateOfBirth": pan_details.get("dob", "").strip(),  # Format: YYYY-MM-DD
                    "PhoneNumber": {
                        "Number": int(phone_number)
                    },
                    "Gender": "Male" if pan_details["gender"].upper() == "M" else "Female"
                },
                "LegalCopyStatus": "Accept",
                "UserConsentForDataSharing": True
            }
            print("cibil payload", cibil_payload)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"CIBIL payload creation failed: {str(e)}")

        cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)

        cibil_data = cibil_resp.json()

        score = intell_response.get("credit_score", {}).get("credit_score")

        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_cibil_logs (
                        pan, dob, name, phone, location, email,
                        raw_report, cibil_score, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pan)
                    DO UPDATE SET
                        dob = EXCLUDED.dob,
                        name = EXCLUDED.name,
                        phone = EXCLUDED.phone,
                        location = EXCLUDED.location,
                        email = EXCLUDED.email,
                        raw_report = EXCLUDED.raw_report,
                        cibil_score = EXCLUDED.cibil_score,
                        created_at = EXCLUDED.created_at
                """, (
                    pan_details.get("pan"),
                    pan_details.get("dob"),
                    f"{pan_details.get('first_name', '')} {pan_details.get('last_name', '')}".strip(),
                    phone_number,
                    pan_details.get("address", {}).get("pin_code"),
                    pan_details.get("email", None),
                    cibil_data,
                    score,
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
            conn.close()
            print("✅ Cibil log saved to database.")
        except Exception as log_err:
            print("❌ Error logging cibil data:", log_err)



        # AI-generated report
        def intell_report():
            try:
                if cibil_data.get("status") == "500":
                    return {"error": "No raw cibil data found"}

                with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmpfile:
                    json.dump(cibil_data, tmpfile)
                    tmpfile.flush()
                    tmpfile_path = tmpfile.name
                with open(tmpfile_path, 'rb') as f:
                        files = {'file': f}
                        resp = requests.post("https://dev-api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
                        resp.raise_for_status()
                        return resp.json()

            except Exception as e:
                return {"error": f"Intelligence report generation failed: {str(e)}"}

        intell_response = intell_report()
        # Check if intell_response is a dictionary and serialize it
        if isinstance(intell_response, dict):
            serialized_intell_response = json.dumps(intell_response)
            print("Serialized intell_response:", serialized_intell_response)  # Debugging
        try:
            # Insert the serialized response into the database
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE user_cibil_logs
                    SET intell_report = %s
                    WHERE pan = %s
                """, (
                    serialized_intell_response,  # Pass the serialized JSON
                    final_pan_number  # The pan number to identify the row to update
                ))
                conn.commit()

            conn.close()
            print("✅ Cibil log saved to database.")
        except Exception as log_err:
            print("❌ Error logging cibil data:", log_err)

        return {
            "pan_number": final_pan_number,
            "pan_supreme": pan_supreme_data,
            "cibil_report": cibil_data,
            "intell_report": intell_response
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
                "cibil_report": fetch_data.get("cibil_report"),
                "intell_report":fetch_data.get("intell_report")
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Verification and data fetch failed: {str(e)}")


