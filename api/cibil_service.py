import json, requests
from datetime import datetime
from fastapi import HTTPException, Body
from time import sleep
import io
from typing import Dict
from models.request_models import LoanFormData
from api.log_utils import log_user_cibil_data
from api.signature import get_signature_headers
from models.request_models import cibilRequest, mandate_cibil
from datetime import datetime, timezone
# from routes.utility_routes import calculate_emi
from db_client import get_db_connection  # make sure this is imported
import re
from fastapi.responses import JSONResponse
import uuid
import tempfile
import requests
import json
import httpx
import traceback
import os
from dotenv import load_dotenv
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta

load_dotenv()

API_1_URL = os.getenv("API_1_URL")
API_2_URL = os.getenv("API_2_URL")
API_3_URL = os.getenv("API_3_URL")
API_4_URL = os.getenv("API_4_URL")
GRIDLINES_PAN_URL = os.getenv("GRIDLINES_PAN_URL")
GRIDLINES_API_KEY = os.getenv("GRIDLINES_API_KEY")
OTP_BASE_URL = os.getenv("OTP_BASE_URL")
BUREAU_PROFILE_URL = os.getenv("BUREAU_PROFILE_URL")
basic_cibil = os.getenv("basic_cibil")

cibil_request_cache = {}

def convert_uuids(obj):
    if isinstance(obj, dict):
        return {k: convert_uuids(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuids(i) for i in obj]
    elif isinstance(obj, uuid.UUID):
        return str(obj)
    else:
        return obj

def calculate_emi_amount(loan_amount: float, roi_string: str, years: int = 20):
    try:
        cleaned_roi = roi_string.strip().split("-")[0].replace("%", "").strip()
        interest_rate = float(cleaned_roi)
        r = interest_rate / (12 * 100)
        n = years * 12
        emi = (loan_amount * r * (1 + r)**n) / ((1 + r)**n - 1)
        return round(emi, 2)
    except Exception as e:
        print("❌ EMI error:", e)
        return None

def initiate_cibil_score(data: cibilRequest):
    score = None
    trans = None
    report = None

    # 🔍 Case 1: Use user-provided cibil score if available
    if data.hascibil == "yes" and data.cibilScore not in [None, 0, ""]:
        score = data.cibilScore
        print(f"✅ Using user-provided cibil score: {score}")
    elif data.hascibil == "no" and data.proceedScoreCheck == "no":
        # Default score for cases where no score is provided and the user doesn't want to proceed
        score = 750  # Default score if user denies cibil score check
        print(f"✅ Using default cibil score: {score}")
    else:
        # 🔍 Case 2: Initiate Equifax cibil fetch
        body = {
            "panNumber": data.panNumber,
            "mobileNumber": data.mobileNumber,
            "firstName": data.firstName,
            "lastName": data.lastName,
            "emailAddress": data.emailAddress,
            "dob": datetime.strptime(data.dob, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00"),
            "gender": data.gender,
            "pinCode": data.pinCode,
            "applicationId": str(data.applicationId) if data.applicationId else None
        }

        # Send request to external cibil service (Equifax)
        headers = get_signature_headers(API_1_URL, "POST", body)
        response = requests.post(API_1_URL, headers=headers, json=body)
        api_data = response.json()

        print(api_data)

        result = api_data.get("result", {})
        score = result.get("cibilScore")
        trans = result.get("transID")

        # Cache the transaction ID for future polling
        if trans:
            cibil_request_cache[trans] = data

        # Handle case when cibil score is not found in the response
        if not score and trans:
            # If score is not available, initiate OTP process and return transaction ID
            return {
                "message": "OTP sent to customer.",
                "transId": trans,
                "cibilScore": None,
                "status": "otp_required"
            }

        # Fetch Equifax report if available
        if score:
            report = fetch_equifax_report_by_pan(data.panNumber)

    # 🔍 Fetch lenders based on cibil score (whether user-provided or from Equifax)
    lenders = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
                    home_loan_ltv, remarks, loan_approval_time, processing_time,
                    minimum_loan_amount, maximum_loan_amount
                FROM lenders
                WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
                AND home_loan_roi IS NOT NULL
                AND home_loan_roi != ''
                ORDER BY CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
            """, (score,))
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
        conn.close()
        for row in rows:
            row_dict = dict(zip(col_names, row))
            if isinstance(row_dict.get("id"), uuid.UUID):
                row_dict["id"] = str(row_dict["id"])
            lenders.append(row_dict)
    except Exception as e:
        print("❌ Error fetching lenders:", e)

    property_name = getattr(data, "propertyName", None)
    approved_lenders = []
    if property_name:
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
                        l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
                        l.minimum_loan_amount, l.maximum_loan_amount
                    FROM approved_projects ap
                    JOIN approved_projects_lenders apl ON apl.id = ap.id
                    JOIN lenders l ON l.id = apl.lender_id
                    WHERE LOWER(ap.project_name) LIKE LOWER(%s)
                """, (f"%{property_name}%",))
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]
                for row in rows:
                    row_dict = dict(zip(col_names, row))
                    if isinstance(row_dict.get("id"), uuid.UUID):
                        row_dict["id"] = str(row_dict["id"])
                    approved_lenders.append(row_dict)
            conn.close()
        except Exception as e:
            print("❌ Error fetching approved lenders:", e)

    approved_ids = {l['id'] for l in approved_lenders}
    remaining_lenders = [l for l in lenders if l.get('id') not in approved_ids]
    combined_lenders = approved_lenders + remaining_lenders
    limited_lenders = combined_lenders[:9]

    form_data = LoanFormData(
        name=f"{data.firstName} {data.lastName}".strip(),
        email=data.emailAddress,
        pan=data.panNumber,
        dob=data.dob,
        phone=data.mobileNumber,
        profession=data.profession,
        loanAmount=data.loanAmount,
        tenureYears=data.tenureYears,
        location=data.pinCode,
        hascibil=data.hascibil or "no",
        cibilScore=score,
        proceedScoreCheck=data.proceedScoreCheck,
        gender=data.gender,
        pin=data.pinCode,
        propertyName=property_name
    )

    emi_data = []
    for lender in limited_lenders:
        roi = lender.get("home_loan_roi")
        emi = calculate_emi_amount(form_data.loanAmount, roi, form_data.tenureYears) if roi else None
        emi_value = emi if emi else "Data Not Available"

        emi_data.append({
            "lender": lender.get("lender_name"),
            "emi": emi_value,
            "lender_type": lender.get("lender_type"),
            "remarks": lender.get("remarks", "Data Not Available"),
            "home_loan_ltv": lender.get("home_loan_ltv", "Data Not Available"),
            "loan_approval_time": lender.get("loan_approval_time", "Data Not Available"),
            "processing_time": lender.get("processing_time", "Data Not Available"),
            "min_loan_amount": lender.get("minimum_loan_amount", "Data Not Available"),
            "max_loan_amount": lender.get("maximum_loan_amount", "Data Not Available")
        })

    def clean_lenders(lenders_list):
        for lender in lenders_list:
            lender.pop('id', None)
        return lenders_list

    approved_lenders = clean_lenders(approved_lenders)
    remaining_lenders = clean_lenders(remaining_lenders)

    # ✅ Final logging with UUID-safe conversion
    log_user_cibil_data(
        form_data,
        convert_uuids({
            "cibilScore": score,
            "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
            "topMatches": lenders[:3],
            "moreLenders": lenders[3:9]
        }),
        convert_uuids(emi_data)
    )

    def intell_report():
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT raw_report FROM user_cibil_logs
                    WHERE pan = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (data.panNumber,))
                result = cur.fetchone()
            conn.close()

            if not result or not result[0]:
                print("❌ No raw cibil data found for PAN")
                return {"error": "No raw cibil data found"}

            raw_json = result[0]
            if not isinstance(raw_json, dict):
                try:
                    raw_json = json.loads(raw_json)  # Just in case it’s a string
                except json.JSONDecodeError:
                    print("❌ Raw data is not valid JSON")
                    return {"error": "Invalid JSON in raw data"}

            # Save raw_json to a temp file
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmpfile:
                json.dump(raw_json, tmpfile)
                tmpfile_path = tmpfile.name

            # Send file to external API
            with open(tmpfile_path, 'rb') as f:
                files = {'file': f}
                resp = requests.post("https://api.orbit.basichomeloan.com/ai/generate_cibil_report", files=files)
                resp.raise_for_status()
                return resp.json()

        except Exception as e:
            print("❌ Error in intell_report:", e)
            return {"error": str(e)}
        
    intell_response = intell_report()

    return {
        "message": "Credit score available. Report and lenders fetched.",
        "cibilScore": score,
        "transId": trans,
        "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
        "approvedLenders": approved_lenders,
        "moreLenders": remaining_lenders,
        "emi_data": emi_data,
        "data": data,
        "intell_response": intell_response

    }

def verify_otp_and_fetch_score(trans_id: str, otp: str, pan: str):
    print("hello", cibil_request_cache)
    res = requests.get(API_2_URL, params={"TransId": trans_id, "Otp": otp}).json()
    if res.get('isError', None) == True:
        result = res.get('responseException', {}).get('exceptionMessage', None)
        return JSONResponse(status_code=400, content=result)
    elif res.get("result").get('cibilStatus') == "InValidOtp":
        return JSONResponse(status_code=400, content="InValidOtp")
    else:
        result = res.get("result")
        print(result)
        if "cibilScore" in result:
            original_request = cibil_request_cache.get(trans_id)
            print(original_request)
            # if not original_request:
            #     return JSONResponse(status_code=400, content={"error": "Original request data not found for transId."})
            return initiate_cibil_score(original_request)
    return {result}

def poll_consent_and_fetch(trans_id: str, pan: str, original_request: cibilRequest, attempts=5, wait=15):
    for attempt in range(1, attempts + 1):
        try:
            print(f"\n⏳ Polling attempt {attempt}/{attempts} for TransID: {trans_id}")
            response = requests.get(API_3_URL, params={"TransId": trans_id}, timeout=10)
            print(f"📶 HTTP {response.status_code} received")
            print("🔍 Raw response text:", response.text)

            res = response.json()
            status = res.get("result", {}).get("status", "")
            print(f"🔁 Consent status: {status}")

            if status == "Complete":
                print("✅ Consent complete. Waiting 10 seconds...")
                sleep(10)

                print("🔁 Re-calling initiate_cibil_score to get full summary...")
                return initiate_cibil_score(original_request)

            # ✅ Optional: Try fetching early
            early_report = fetch_equifax_report_by_pan(pan)
            if early_report and early_report.get("equifaxScore"):
                print("✅ Score found early by PAN, stopping polling.")
                return early_report

        except Exception as e:
            print(f"⚠️ Error during polling attempt {attempt}: {e}")

        sleep(wait)

    return {"message": "Consent not completed and score not found after polling."}

def fetch_equifax_report_by_pan(pan_number: str):
    try:
        # Step 1: Construct full API URL
        full_url = f"{API_4_URL}?PanNumber={pan_number}&includeReportJson=true"
        print(f"\n🔗 API 4 URL: {full_url}")

        # Step 2: Sign request
        headers = get_signature_headers(full_url.lower(), "GET", None)

        # Step 3: Make the API call
        response = requests.get(full_url, headers=headers)
        final_data = response.json()
        print(final_data)
        report_json = final_data
        return {
            "equifaxScore": report_json.get("result").get("customercibilScore"),
            "raw": final_data  # ✅ this ensures everything is saved/logged
        }

    except Exception as e:
        print(f"❌ Exception in API 4 call: {e}")
        return {
            "error": str(e),
            "raw": None
        }

GRIDLINES_HEADERS = {
    "X-API-Key": "FD0SgdtM6KIw8p2sJYv7ObMuvuezZLw7",
    "X-Auth-Type": "API-Key",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

def send_otp_to_user(phone_number: str):
    try:
        response = requests.post(f"{OTP_BASE_URL}/otp_send", json={"phone_number": phone_number})
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OTP send failed: {e}")
    
async def resend_otp_to_user(phone_number: str):
    async with httpx.AsyncClient() as client:
        try:
            print(f"🔁 Resending OTP to phone number: {phone_number}")
            resend_response = await client.post(
                f"{OTP_BASE_URL}/otp_resend",
                json={"phone_number": phone_number}
            )
            resend_data = resend_response.json()
            print(f"✅ OTP Resend Response [{resend_response.status_code}]: {resend_data}")

            if resend_response.status_code != 200 or not resend_data.get("success"):
                return {"status": "N", "message": "Failed to resend OTP"}

            return {"status": "Y", "message": "OTP resent successfully"}

        except Exception as e:
            print(f"❌ Exception occurred while resending OTP: {e}")
            raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")
        
STATE_CODE_MAP = {
    "ANDHRA PRADESH": "AP",
    "ARUNACHAL PRADESH": "AR",
    "ASSAM": "AS",
    "BIHAR": "BR",
    "CHHATTISGARH": "CG",
    "GOA": "GA",
    "GUJARAT": "GJ",
    "HARYANA": "HR",
    "HIMACHAL PRADESH": "HP",
    "JHARKHAND": "JH",
    "KARNATAKA": "KA",
    "KERALA": "KL",
    "MADHYA PRADESH": "MP",
    "MAHARASHTRA": "MH",
    "MANIPUR": "MN",
    "MEGHALAYA": "ML",
    "MIZORAM": "MZ",
    "NAGALAND": "NL",
    "ODISHA": "OR",
    "PUNJAB": "PB",
    "RAJASTHAN": "RJ",
    "SIKKIM": "SK",
    "TAMIL NADU": "TN",
    "TELANGANA": "TS",
    "TRIPURA": "TR",
    "UTTAR PRADESH": "UP",
    "UTTARAKHAND": "UT",
    "WEST BENGAL": "WB",
    "DELHI": "DL",
    "JAMMU AND KASHMIR": "JK"
}


# async def send_and_verify_pan(phone_number: str, otp: str, pan_number: str):
#     async with httpx.AsyncClient(timeout=60.0) as client:
#         try:
#             # Step 1: OTP Verification
#             print(f"🔍 Verifying OTP for {phone_number} with OTP: {otp}")
#             verify_response = await client.post(
#                 f"{OTP_BASE_URL}/verify",
#                 json={"phone_number": phone_number, "otp": otp}
#             )
#             verify_data = verify_response.json()
#             print(f"✅ OTP Verify Response [{verify_response.status_code}]: {verify_data}")
#             if verify_response.status_code != 200 or not verify_data.get("success"):
#                 return {"consent": "N", "message": "OTP verification failed"}

#             # Step 2: PAN Fetch
#             print(f"🔗 Fetching PAN details for: {pan_number}")
#             pan_response = await client.post(
#                 GRIDLINES_PAN_URL,
#                 headers=GRIDLINES_HEADERS,
#                 json={"pan_number": pan_number, "consent": "Y"}
#             )
#             print(f"✅ PAN Fetch Response [{pan_response.status_code}]: {pan_response.text}")
#             if pan_response.status_code != 200:
#                 return {"consent": "N", "message": "PAN fetch failed", "error": pan_response.text}

#             pan_data = pan_response.json()
#             raw_state = pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("state", "DELHI")
#             mapped_state = STATE_CODE_MAP.get(raw_state.upper(), "DL")

#             # Step 3: Bureau Profile
#             print("📋 Building Bureau profile payload...")
#             bureau_payload = {
#                 "phone": phone_number[-10:],
#                 "full_name": pan_data.get("data", {}).get("pan_data", {}).get("name"),
#                 "date_of_birth": pan_data.get("data", {}).get("pan_data", {}).get("date_of_birth"),
#                 "pan": pan_data.get("data", {}).get("pan_data", {}).get("document_id"),
#                 "address": pan_data.get("address", "NA"),
#                 "state": mapped_state,
#                 "pincode": pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("pincode"),
#                 "consent": "Y"
#             }
#             print(f"📨 Sending Bureau Profile Request: {bureau_payload}")
#             bureau_response = await client.post(
#                 BUREAU_PROFILE_URL,
#                 headers=GRIDLINES_HEADERS,
#                 json=bureau_payload
#             )
#             print(f"✅ Bureau Profile Response [{bureau_response.status_code}]: {bureau_response.text}")
#             if bureau_response.status_code != 200:
#                 return {"consent": "Y", "message": "Bureau profile fetch failed", "error": bureau_response.text}

#             bureau_json = bureau_response.json()
#             score = None
#             score_details = bureau_json.get("data", {}).get("profile_data", {}).get("score_detail", [])
#             print("📊 Score details:", score_details)

#             for item in score_details:
#                 if item.get("type") == "ERS" and item.get("version") == "4.0":
#                     score = item.get("value")
#                     break

#             print("✅ Extracted Score:", score)
#             if not score:
#                 return {"consent": "Y", "message": "cibil score not found in bureau response"}

#             # 🧠 Optional: Prepare dummy/empty values to return as placeholders
#             trans = bureau_json.get("transaction_id", "")
#             raw = bureau_json
#             approved_lenders = []
#             remaining_lenders = []
#             emi_data = {}
#             data = bureau_json.get("data")
#             raw_report_data = None

#             # ✅ Log cibil data
#             try:
#                 conn = get_db_connection()
#                 with conn.cursor() as cur:
#                     cur.execute("""
#                         INSERT INTO user_cibil_logs (
#                             pan, dob, name, phone, location, email,
#                             raw_report, cibil_score, created_at
#                         ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
#                         ON CONFLICT (pan)
#                         DO UPDATE SET
#                             dob = EXCLUDED.dob,
#                             name = EXCLUDED.name,
#                             phone = EXCLUDED.phone,
#                             location = EXCLUDED.location,
#                             email = EXCLUDED.email,
#                             raw_report = EXCLUDED.raw_report,
#                             cibil_score = EXCLUDED.cibil_score,
#                             created_at = EXCLUDED.created_at
#                     """, (
#                         pan_data.get("data", {}).get("pan_data", {}).get("document_id"),
#                         pan_data.get("data", {}).get("pan_data", {}).get("date_of_birth"),
#                         pan_data.get("data", {}).get("pan_data", {}).get("name"),
#                         phone_number,
#                         pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("pincode"),
#                         pan_data.get("data", {}).get("pan_data", {}).get("email", None),
#                         json.dumps(raw),
#                         score,
#                         datetime.now(timezone.utc)
#                     ))
#                     conn.commit()
#                 conn.close()
#                 print("✅ cibil log saved to database.")
#             except Exception as log_err:
#                 print("❌ Error logging cibil data:", log_err)

#             #Fetch score from DB
#             try:
#                 conn = get_db_connection()
#                 with conn.cursor() as cur:
#                     cur.execute("""
#                         SELECT raw_report
#                         FROM user_cibil_logs
#                         WHERE pan = %s ORDER BY created_at DESC LIMIT 1
#                     """, (pan_number))
#                     result = cur.fetchone()
#                 conn.close()
#                 print("hello this is the result", result)
#                 if result and result[0]:
#                     raw_report_data = result[0] if isinstance(result[0], dict) else json.loads(result[0])

#                     # score1 = int(raw_report_data.get("cibilScore", score or 750))
#                 # else:
#                 #     score1 = score or 750
#             except Exception as e:
#                 score1 = score or 750

#             #AI-generated report
#             def intell_report():
#                 try:
#                     if not raw_report_data:
#                         return {"error": "No raw cibil data found"}
#                     with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmpfile:
#                         json.dump(raw_report_data, tmpfile)
#                         tmpfile_path = tmpfile.name
#                     with open(tmpfile_path, 'rb') as f:
#                         files = {'file': f}
#                         resp = requests.post("https://api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
#                         resp.raise_for_status()
#                         return resp.json()
#                 except Exception as e:
#                     return {"error": str(e)}

#             intell_response = intell_report()

#             return {
#                 "message": "Credit score available. Report and lenders fetched.",
#                 "cibilScore": score,
#                 "transId": trans,
#                 "raw": raw,
#                 "approvedLenders": approved_lenders,
#                 "moreLenders": remaining_lenders,
#                 "emi_data": emi_data,
#                 "data": data,
#                 "intell_response": intell_response
#             }

#         except Exception as e:
#             print("❌ Exception occurred while verifying PAN/Bureau:")
#             traceback.print_exc()
#             raise HTTPException(status_code=500, detail="Server error during PAN/cibil process")
        

async def send_and_verify_pan(phone_number: str, otp: str , pan_number: str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            if otp != "NA":
                print(f"🔍 Verifying OTP for {phone_number} with OTP: {otp}")
                verify_response = await client.post(
                    f"{OTP_BASE_URL}/otp_verify",
                    json={"phone_number": phone_number, "otp": otp}
                )
                verify_data = verify_response.json()
                print(f"✅ OTP Verify Response [{verify_response.status_code}]: {verify_data}")
                if verify_response.status_code != 200 or not verify_data.get("success"):
                    return {"consent": "N", "message": "OTP verification failed"}
            else:
                print("✅ Skipping OTP verification since already done.")
            # Step 2: PAN Fetch
            print(f"🔗 Fetching PAN details for: {pan_number}")
            pan_response = await client.post(
                GRIDLINES_PAN_URL,
                headers=GRIDLINES_HEADERS,
                json={"pan_number": pan_number, "consent": "Y"}
            )
            print(f"✅ PAN Fetch Response [{pan_response.status_code}]: {pan_response.text}")
            if pan_response.status_code != 200:
                return {"consent": "N", "message": "PAN fetch failed", "error": pan_response.text}

            pan_data = pan_response.json()
            raw_state = pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("state", "DELHI")
            mapped_state = STATE_CODE_MAP.get(raw_state.upper(), "DL")


            # Step 3: Bureau Profile
            print("📋 Building Bureau profile payload...")
            bureau_payload = {
                "phone": phone_number[-10:],
                "full_name": pan_data.get("data", {}).get("pan_data", {}).get("name"),
                "date_of_birth": pan_data.get("data", {}).get("pan_data", {}).get("date_of_birth"),
                "pan": pan_data.get("data", {}).get("pan_data", {}).get("document_id"),
                "address": pan_data.get("address", "NA"),
                "state": mapped_state,
                "pincode": pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("pincode") or "201310",
                "consent": "Y"
            }
            print(f"📨 Sending Bureau Profile Request: {bureau_payload}")
            bureau_response = await client.post(
                BUREAU_PROFILE_URL,
                headers=GRIDLINES_HEADERS,
                json=bureau_payload
            )
            print(f"✅ Bureau Profile Response [{bureau_response.status_code}]: {bureau_response.text}")
            if bureau_response.status_code != 200:
                return {"consent": "Y", "message": "Bureau profile fetch failed", "error": bureau_response.text}

            bureau_json = bureau_response.json()
            score = None
            score_details = bureau_json.get("data", {}).get("profile_data", {}).get("score_detail", [])
            print("📊 Score details:", score_details)

            for item in score_details:
                if item.get("type") == "ERS" and item.get("version") == "4.0":
                    score = item.get("value")
                    break

            print("✅ Extracted Score:", score)
            if not score:
                return {"consent": "Y", "message": "Cibil score not found in bureau response"}

            # 🧠 Optional: Prepare dummy/empty values to return as placeholders
            trans = bureau_json.get("transaction_id", "")
            raw = bureau_json
            print("raw", raw)
            approved_lenders = []
            remaining_lenders = []
            # emi_data = {}
            data = bureau_json.get("data")
            raw_report_data = None

            # --- Active EMI sum from the bureau profile response ---
            try:
                acct_summary = (raw.get("data") or {}).get("profile_data", {}).get("account_summary", {})  # <- correct path
                mpa_str = (acct_summary.get("total_monthly_payment_amount") or "0").replace(",", "")
                active_emi_sum = max(0.0, float(mpa_str))  # clamp negatives to 0
            except Exception:
                active_emi_sum = 0.0

            consent = "Y"


            try:
                conn = get_db_connection()
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_cibil_logs (
                            pan, dob, name, phone, location, email,
                            raw_report, cibil_score, created_at, monthly_emi, consent, source, gender
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,%s)
                        ON CONFLICT (pan)
                        DO UPDATE SET
                            dob = EXCLUDED.dob,
                            name = EXCLUDED.name,
                            phone = EXCLUDED.phone,
                            location = EXCLUDED.location,
                            email = EXCLUDED.email,
                            raw_report = EXCLUDED.raw_report,
                            cibil_score = EXCLUDED.cibil_score,
                            created_at = EXCLUDED.created_at,
                            monthly_emi = EXCLUDED.monthly_emi,
                            consent = EXCLUDED.consent,
                            source = EXCLUDED.source,
                            gender = EXCLUDED.gender
                    """, (
                        pan_data.get("data", {}).get("pan_data", {}).get("document_id"),
                        pan_data.get("data", {}).get("pan_data", {}).get("date_of_birth"),
                        pan_data.get("data", {}).get("pan_data", {}).get("name"),
                        phone_number,
                        pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("pincode"),
                        pan_data.get("data", {}).get("pan_data", {}).get("email", None),
                        json.dumps(raw),
                        score,
                        datetime.now(timezone.utc),
                        active_emi_sum,
                        consent,
                        "Equifax",
                        data.get("profile_data", {}).get("personal_information", {}).get("gender", "")
                    ))
                    conn.commit()
                conn.close()
                print("✅ Cibil log saved to database.")
            except Exception as log_err:
                print("❌ Error logging cibil data:", log_err)

            # Debugging the raw_report_data retrieval process
            # print(f"Fetching raw CIBIL data for PAN {pan_number}")
            # try:
            #     conn = get_db_connection()
            #     with conn.cursor() as cur:
            #         cur.execute("""
            #             SELECT raw_report FROM user_cibil_logs WHERE pan = %s ORDER BY created_at DESC LIMIT 1
            #         """, (pan_number,))
            #         result = cur.fetchone()
            #     conn.close()
            #     # print(f"Raw CIBIL Data: {result}")
            #     raw_report_data = result
            # except Exception as e:
            #     print("❌ Error fetching raw report:", e)

            # AI-generated report
            # def intell_report():
            #     try:
            #         if not raw_report_data:
            #             return {"error": "No raw cibil data found"}
            #         with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmpfile:
            #             json.dump(raw_report_data, tmpfile)
            #             tmpfile_path = tmpfile.name
            #         with open(tmpfile_path, 'rb') as f:
            #             files = {'file': f}
            #             resp = requests.post("https://api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
            #             resp.raise_for_status()
            #             return resp.json()
            #     except Exception as e:
            #         return {"error": str(e)}

            # intell_response = intell_report()

            # try:
            #     # Check if intell_response is a dictionary and serialize it
            #     if isinstance(intell_response, dict):
            #         serialized_intell_response = json.dumps(intell_response)
            #         print("Serialized intell_response:", serialized_intell_response)  # Debugging

            #     # Insert the serialized response into the database
            #     conn = get_db_connection()
            #     with conn.cursor() as cur:
            #         cur.execute("""
            #             UPDATE user_cibil_logs
            #             SET intell_report = %s
            #             WHERE pan = %s
            #         """, (
            #             serialized_intell_response,  # Pass the serialized JSON
            #             pan_number  # The pan number to identify the row to update
            #         ))
            #         conn.commit()

            #     conn.close()
            #     print("✅ Cibil log saved to database.")
            # except Exception as log_err:
            #     print("❌ Error logging cibil data:", log_err)
            dob_raw = data.get("profile_data", {}).get("personal_information", {}).get("date_of_birth", "")
            dob_clean = dob_raw.split("+")[0] if "+" in dob_raw else dob_raw

            # Convert to dd-mm-yyyy format
            dob_formatted = ""
            if dob_clean:
                try:
                    dob_obj = datetime.strptime(dob_clean, "%Y-%m-%d")
                    dob_formatted = dob_obj.strftime("%d-%m-%Y")
                except ValueError:
                    dob_formatted = dob_clean  # fallback in case parsing fails
            
            user_details = {
                "dob": dob_formatted,
                # "credit_score": data.get("score_detail",[{}])[0].get("value", ""),
                "credit_score": score,
                "email": data.get("profile_data", {}).get("email", [{}])[0].get("value", ""),
                "gender": data.get("profile_data", {}).get("personal_information", {}).get("gender", ""),
                "pan_number": data.get("profile_data", {}).get("national_document_data", {}).get("pan", [{}])[0].get("value", ""),
                "pincode": data.get("profile_data", {}).get("address", [{}])[0].get("pincode", ""),
                "name": data.get("profile_data", {}).get("personal_information", {}).get("full_name", ""),
                "phone": data.get("profile_data", {}).get("phone", [{}])[0].get("value", "")
            }

            
            return {
                "message": "Credit score available. Report and lenders fetched.",
                "cibilScore": score,
                "transId": trans,
                "raw": raw,
                "approvedLenders": approved_lenders,
                "moreLenders": remaining_lenders,
                "data": data,
                # "intell_response": intell_response    
                "emi_data": active_emi_sum,
                "user_details": user_details,
                "source": "Equifax"
            }

        except Exception as e:
            print("❌ Exception occurred while verifying PAN/Bureau:", e)
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="Server error during PAN/cibil process")


async def fetch_lenders_and_emi(data: LoanFormData):
    import re, json, tempfile, uuid, requests

    def to_canonical(name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

    def clean_lenders(lenders_list):
        for lender in lenders_list:
            lender.pop('id', None)
        return lenders_list

    def convert_uuids(obj):
        if isinstance(obj, list):
            return [convert_uuids(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: (str(v) if isinstance(v, uuid.UUID) else convert_uuids(v)) for k, v in obj.items()}
        return obj

    score = None
    pan = data.pan
    property_name = data.propertyName
    canonical_property = to_canonical(property_name)
    lenders, approved_lenders = [], []

    # Step 1: Fetch score from DB
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT raw_report
                FROM user_cibil_logs
                WHERE pan = %s ORDER BY created_at DESC LIMIT 1
            """, (pan,))
            result = cur.fetchone()
        conn.close()
        if result and result[0]:
            raw_report_data = result[0] if isinstance(result[0], dict) else json.loads(result[0])
            score = int(raw_report_data.get("cibilScore", data.cibilScore or 750))
        else:
            score = data.cibilScore or 750
    except Exception as e:
        score = data.cibilScore or 750

    # Step 2: Fetch matching lenders by CIBIL
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
                    home_loan_ltv, remarks, loan_approval_time, processing_time,
                    minimum_loan_amount, maximum_loan_amount
                FROM lenders
                WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
                AND home_loan_roi IS NOT NULL AND home_loan_roi != ''
                ORDER BY CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
            """, (score,))
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
        conn.close()
        for row in rows:
            row_dict = dict(zip(col_names, row))
            if isinstance(row_dict.get("id"), uuid.UUID):
                row_dict["id"] = str(row_dict.get("id"))
            lenders.append(row_dict)
    except Exception as e:
        print("❌ Error fetching lenders:", e)

    # Step 3: Fetch approved lenders by canonical name
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
                    l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
                    l.minimum_loan_amount, l.maximum_loan_amount
                FROM approved_projects ap
                JOIN approved_projects_lenders apl ON apl.project_id = ap.id
                JOIN lenders l ON l.id = apl.lender_id
                WHERE LOWER(ap.canonical_name) = LOWER(%s)
            """, (canonical_property,))
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
        conn.close()
        for row in rows:
            row_dict = dict(zip(col_names, row))
            if isinstance(row_dict.get("id"), uuid.UUID):
                row_dict["id"] = str(row_dict.get("id"))
            approved_lenders.append(row_dict)
    except Exception as e:
        print("❌ Error fetching approved lenders:", e)

    # Step 4: Filter more lenders
    approved_ids = {l['id'] for l in approved_lenders}
    remaining_lenders = [l for l in lenders if l.get('id') not in approved_ids]
    combined_lenders = approved_lenders + remaining_lenders
    limited_lenders = combined_lenders[:9]

    # Step 5: Final selection
    approved_lenders_final = approved_lenders[:5]
    limited_lenders = approved_lenders_final + remaining_lenders

    # Step 6: Calculate EMI
    emi_data = []
    for lender in limited_lenders:
        roi = lender.get("home_loan_roi")
        emi = calculate_emi_amount(data.loanAmount, roi, data.tenureYears) if roi else "8.5"
        emi_value = emi if emi else "Data Not Available"
        emi_data.append({
            "lender": lender.get("lender_name"),
            "emi": emi_value if emi_value else "Data Not Available",
            "lender_type": lender.get("lender_type"),
            "remarks": lender.get("remarks", "Data Not Available"),
            "home_loan_ltv": lender.get("home_loan_ltv", "Data Not Available"),
            "loan_approval_time": lender.get("loan_approval_time", "Data Not Available"),
            "processing_time": lender.get("processing_time", "Data Not Available"),
            "min_loan_amount": lender.get("minimum_loan_amount", "Data Not Available"),
            "max_loan_amount": lender.get("maximum_loan_amount", "Data Not Available")
        })

    # Step 7: Log
    log_user_cibil_data(
        data,
        convert_uuids({
            "cibilScore": score,
            "raw": raw_report_data or "User-provided score; Equifax skipped",
            "topMatches": lenders[:3],
            "moreLenders": remaining_lenders[:6]
        }),
        convert_uuids(emi_data)
    )

    

    return {
        "message": "Lenders fetched and EMI calculated successfully",
        "cibilScore": score,
        "approvedLenders": clean_lenders(approved_lenders_final),
        "moreLenders": clean_lenders(remaining_lenders),
        "emi_data": emi_data
    }


PRIORITY_ORDER = [
    "SBI",                 # State Bank of India
    "HDFC",
    "ICICI",
    "Axis",
    "Bank of Baroda",
    "Canara Bank",
]

def _norm(name: str) -> str:
    """Lowercase, strip punctuation, normalize spacing."""
    return re.sub(r'[^a-z0-9 ]', '', (name or "").lower()).strip()

def _priority_key(name: str) -> str | None:
    n = _norm(name)

    if "sbi" in n or "state bank of india" in n:
        return "SBI"
    if "hdfc" in n:  # covers "hdfc ltd.", "hdfc ltd (housing...)", "hdfc bank"
        return "HDFC"
    if "icici" in n:  # covers "icici bank limited", "icici home finance"
        return "ICICI"
    if "axis" in n:   # covers "axis bank ltd."
        return "Axis"
    if "bank of baroda" in n or "bob" in n:
        return "Bank of Baroda"
    if "canara" in n:
        return "Canara Bank"

    return None

def _idset(xs):
    return {x.get("id") for x in xs if x.get("id")}

def _nameset(xs):
    return {_norm(x.get("lender_name", "")) for x in xs if x.get("lender_name")}

def _clean_lenders(lenders_list):
    out = []
    for lender in lenders_list:
        d = dict(lender)
        d.pop('id', None)   # remove id only from payload, not from internal logic
        out.append(d)
    return out

# async def fetch_lenders_apf(propertyName: str, score: int = 750):
#     def to_canonical(name: str) -> str:
#         return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

#     canonical_property = to_canonical(propertyName)

#     lenders, approved_lenders = [], []

#     # 1) LENDERS by CIBIL
#     conn = None
#     try:
#         conn = get_db_connection()
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
#                        home_loan_ltv, remarks, loan_approval_time, processing_time,
#                        minimum_loan_amount, maximum_loan_amount
#                 FROM lenders
#                 WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
#                   AND home_loan_roi IS NOT NULL AND home_loan_roi <> ''
#                 ORDER BY CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
#             """, (score,))
#             rows = cur.fetchall()
#             col_names = [desc[0] for desc in cur.description]
#         for row in rows:
#             row_dict = dict(zip(col_names, row))
#             if isinstance(row_dict.get("id"), uuid.UUID):
#                 row_dict["id"] = str(row_dict["id"])
#             lenders.append(row_dict)
#     except Exception as e:
#         print("❌ Error fetching lenders:", e)
#     finally:
#         if conn:
#             conn.close()

#     # 2) APF-APPROVED lenders
#     conn = None
#     try:
#         conn = get_db_connection()
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT DISTINCT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
#                                 l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
#                                 l.minimum_loan_amount, l.maximum_loan_amount
#                 FROM approved_projects ap
#                 JOIN approved_projects_lenders apl ON apl.project_id = ap.id
#                 JOIN lenders l ON l.id = apl.lender_id
#                 WHERE ap.canonical_name = %s
#             """, (canonical_property,))
#             rows = cur.fetchall()
#             col_names = [desc[0] for desc in cur.description]
#         for row in rows:
#             row_dict = dict(zip(col_names, row))
#             if isinstance(row_dict.get("id"), uuid.UUID):
#                 row_dict["id"] = str(row_dict["id"])
#             approved_lenders.append(row_dict)
#     except Exception as e:
#         print("❌ Error fetching approved lenders:", e)
#     finally:
#         if conn:
#             conn.close()

#     # 3) MERGE (APF first then CIBIL), dedupe by id and name
#     merged = []
#     seen_ids = set()
#     seen_names = set()
#     for src in (approved_lenders, lenders):
#         for item in src:
#             lid = item.get("id")
#             lname = _norm(item.get("lender_name", ""))
#             if lid and lid in seen_ids:
#                 continue
#             if not lid and lname in seen_names:
#                 continue
#             merged.append(item)
#             if lid:
#                 seen_ids.add(lid)
#             if lname:
#                 seen_names.add(lname)

#     # 4) Build APPROVED (highest precedence)
#     approved_ids = _idset(approved_lenders)
#     approved_names = _nameset(approved_lenders)

#     # 5) From remaining, pick WORKING (priority banks) in your order
#     buckets = {k: None for k in PRIORITY_ORDER}  # preserve order
#     non_approved_pool = []
#     for item in merged:
#         lid = item.get("id")
#         lname = _norm(item.get("lender_name", ""))

#         # exclude anything already in approved
#         if (lid and lid in approved_ids) or (lname and lname in approved_names):
#             continue

#         key = _priority_key(item.get("lender_name", ""))
#         if key and buckets[key] is None:
#             buckets[key] = item
#         else:
#             non_approved_pool.append(item)

#     working_lenders = [buckets[k] for k in PRIORITY_ORDER if buckets[k] is not None]

#     # 6) MORE LENDERS = merged minus (approved ∪ working)
#     working_ids = _idset(working_lenders)
#     working_names = _nameset(working_lenders)

#     more_lenders = []
#     for item in merged:
#         lid = item.get("id")
#         lname = _norm(item.get("lender_name", ""))

#         if (lid and lid in approved_ids) or (lname and lname in approved_names):
#             continue
#         if (lid and lid in working_ids) or (lname and lname in working_names):
#             continue

#         more_lenders.append(item)

#     # 7) Optional cap (e.g., keep response lean: 6 working + up to 3 more = 9)
#     total_cap = 13
#     if len(working_lenders) >= total_cap:
#         more_lenders = []
#     else:
#         more_lenders = more_lenders[: total_cap - len(working_lenders)]

#     return {
#         "message": "Lenders fetched successfully",
#         "cibilScore": score,
#         "approvedLenders": _clean_lenders(approved_lenders),
#         "workingLenders": _clean_lenders(working_lenders),
#         "moreLenders": _clean_lenders(more_lenders),
#     }


# async def fetch_lenders_apf(propertyName: str, score: int = 750):
#     def to_canonical(name: str) -> str:
#         return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

#     def parse_roi(roi_str: str):
#         """
#         Extract the first numeric value from a ROI string.
#         Examples:
#             "7.50 p.a. onwards" -> 7.5
#             "7.50-8.25%" -> 7.5
#             "N/A" -> None
#         """
#         if not roi_str:
#             return None
#         match = re.search(r'\d+(\.\d+)?', roi_str)
#         return float(match.group()) if match else None

#     canonical_property = to_canonical(propertyName)
#     lenders, approved_lenders = [], []

#     # --- 1) LENDERS by CIBIL ---
#     conn = None
#     try:
#         conn = get_db_connection()
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
#                        home_loan_ltv, remarks, loan_approval_time, processing_time,
#                        minimum_loan_amount, maximum_loan_amount, minimum_credit_score
#                 FROM lenders
#                 WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
#                   AND home_loan_roi IS NOT NULL AND home_loan_roi <> ''
#             """, (score,))
#             rows = cur.fetchall()
#             col_names = [desc[0] for desc in cur.description]
#         for row in rows:
#             row_dict = dict(zip(col_names, row))
#             if isinstance(row_dict.get("id"), uuid.UUID):
#                 row_dict["id"] = str(row_dict["id"])
#             # parse ROI into float for sorting
#             row_dict["home_loan_roi_float"] = parse_roi(row_dict.get("home_loan_roi"))
#             lenders.append(row_dict)
#         # sort by numeric ROI ascending
#         lenders.sort(key=lambda x: (x["home_loan_roi_float"] is None, x["home_loan_roi_float"]))
#     except Exception as e:
#         print("❌ Error fetching lenders:", e)
#     finally:
#         if conn:
#             conn.close()

#     # --- 2) APF-APPROVED lenders ---
#     conn = None
#     try:
#         conn = get_db_connection()
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT DISTINCT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
#                                 l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
#                                 l.minimum_loan_amount, l.maximum_loan_amount
#                 FROM approved_projects ap
#                 JOIN approved_projects_lenders apl ON apl.project_id = ap.id
#                 JOIN lenders l ON l.id = apl.lender_id
#                 WHERE ap.canonical_name = %s
#             """, (canonical_property,))
#             rows = cur.fetchall()
#             col_names = [desc[0] for desc in cur.description]
#         for row in rows:
#             row_dict = dict(zip(col_names, row))
#             if isinstance(row_dict.get("id"), uuid.UUID):
#                 row_dict["id"] = str(row_dict["id"])
#             row_dict["home_loan_roi_float"] = parse_roi(row_dict.get("home_loan_roi"))
#             approved_lenders.append(row_dict)
#         approved_lenders.sort(key=lambda x: (x["home_loan_roi_float"] is None, x["home_loan_roi_float"]))
#     except Exception as e:
#         print("❌ Error fetching approved lenders:", e)
#     finally:
#         if conn:
#             conn.close()

#     # --- 3) MERGE (APF first then CIBIL), dedupe by id and name ---
#     merged = []
#     seen_ids = set()
#     seen_names = set()
#     for src in (approved_lenders, lenders):
#         for item in src:
#             lid = item.get("id")
#             lname = _norm(item.get("lender_name", ""))
#             if lid and lid in seen_ids:
#                 continue
#             if not lid and lname in seen_names:
#                 continue
#             merged.append(item)
#             if lid:
#                 seen_ids.add(lid)
#             if lname:
#                 seen_names.add(lname)

#     # --- 4) Build APPROVED (highest precedence) ---
#     approved_ids = _idset(approved_lenders)
#     approved_names = _nameset(approved_lenders)

#     # --- 5) From remaining, pick WORKING (priority banks) ---
#     buckets = {k: None for k in PRIORITY_ORDER}  # preserve order
#     non_approved_pool = []
#     for item in merged:
#         lid = item.get("id")
#         lname = _norm(item.get("lender_name", ""))
#         if (lid and lid in approved_ids) or (lname and lname in approved_names):
#             continue

#         key = _priority_key(item.get("lender_name", ""))
#         if key and buckets[key] is None:
#             buckets[key] = item
#         else:
#             non_approved_pool.append(item)

#     working_lenders = [buckets[k] for k in PRIORITY_ORDER if buckets[k] is not None]

#     # --- 6) MORE LENDERS = merged minus (approved ∪ working) ---
#     working_ids = _idset(working_lenders)
#     working_names = _nameset(working_lenders)

#     more_lenders = []
#     for item in merged:
#         lid = item.get("id")
#         lname = _norm(item.get("lender_name", ""))
#         if (lid and lid in approved_ids) or (lname and lname in approved_names):
#             continue
#         if (lid and lid in working_ids) or (lname and lname in working_names):
#             continue
#         more_lenders.append(item)

#     # --- 7) Optional cap (e.g., 6 working + up to 7 more = 13 total) ---
#     total_cap = 13
#     if len(working_lenders) >= total_cap:
#         more_lenders = []
#     else:
#         more_lenders = more_lenders[: total_cap - len(working_lenders)]

#     # --- 8) Clean response ---
#     return {
#         "message": "Lenders fetched successfully",
#         "cibilScore": score,
#         "approvedLenders": _clean_lenders(approved_lenders),
#         "workingLenders": _clean_lenders(working_lenders),
#         "moreLenders": _clean_lenders(more_lenders),
#     }

async def fetch_lenders_apf(propertyName: str, score: int = 750):
    def to_canonical(name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

    def parse_roi(roi_str: str):
        """
        Extract the first numeric value from a ROI string.
        Examples:
            "7.50 p.a. onwards" -> 7.5
            "7.50-8.25%" -> 7.5
            "N/A" -> None
        """
        if not roi_str:
            return None
        match = re.search(r'\d+(\.\d+)?', roi_str)
        return float(match.group()) if match else None

    canonical_property = to_canonical(propertyName)
    lenders, approved_lenders = [], []

    # --- 1) LENDERS by CIBIL ---
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
                       home_loan_ltv, remarks, loan_approval_time, processing_time,
                       minimum_loan_amount, maximum_loan_amount, minimum_credit_score
                FROM lenders
                WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
                  AND home_loan_roi IS NOT NULL AND home_loan_roi <> ''
            """, (score,))
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

        for row in rows:
            row_dict = dict(zip(col_names, row))
            if isinstance(row_dict.get("id"), uuid.UUID):
                row_dict["id"] = str(row_dict["id"])
            # parse ROI into float for sorting
            row_dict["home_loan_roi_float"] = parse_roi(row_dict.get("home_loan_roi"))
            lenders.append(row_dict)

        # sort numerically by ROI ascending
        lenders.sort(key=lambda x: (x["home_loan_roi_float"] is None, x["home_loan_roi_float"]))

    except Exception as e:
        print("❌ Error fetching lenders:", e)
    finally:
        if conn:
            conn.close()

    # # --- 2) APF-APPROVED lenders ---
    # conn = None
    # try:
    #     conn = get_db_connection()
    #     with conn.cursor() as cur:
    #         cur.execute("""
    #             SELECT DISTINCT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
    #                             l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
    #                             l.minimum_loan_amount, l.maximum_loan_amount
    #             FROM approved_projects ap
    #             JOIN approved_projects_lenders apl ON apl.project_id = ap.id
    #             JOIN lenders l ON l.id = apl.lender_id
    #             WHERE ap.canonical_name = %s
    #         """, (canonical_property,))
    #         rows = cur.fetchall()
    #         col_names = [desc[0] for desc in cur.description]

    #     for row in rows:
    #         row_dict = dict(zip(col_names, row))
    #         if isinstance(row_dict.get("id"), uuid.UUID):
    #             row_dict["id"] = str(row_dict["id"])
    #         row_dict["home_loan_roi_float"] = parse_roi(row_dict.get("home_loan_roi"))
    #         approved_lenders.append(row_dict)

    #     approved_lenders.sort(key=lambda x: (x["home_loan_roi_float"] is None, x["home_loan_roi_float"]))

    # except Exception as e:
    #     print("❌ Error fetching approved lenders:", e)
    # finally:
    #     if conn:
    #         conn.close()

    
    # --- 2) APF-APPROVED lenders (PATCH: uses only propertyName) ---
    # --- 2) APF-APPROVED lenders (single-CTE, identical to your SQL) ---
    approved_lenders = []
    conn = None
    try:
        canonical_property = to_canonical(propertyName)  # "M3M Merlin" -> "m3mmerlin"
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                WITH proj AS (
                SELECT id
                FROM public.approved_projects
                WHERE project_name_canon = %s
                    OR btrim(lower(canonical_name)) = %s
                ORDER BY updated_at DESC
                LIMIT 1
                ),
                m AS (
                SELECT apl.lender_id
                FROM public.approved_projects_lenders apl
                JOIN proj p ON p.id = apl.project_id
                ),
                j AS (
                SELECT l.id, l.lender_name, l.lender_type,
                        l.home_loan_roi, l.lap_roi, l.home_loan_ltv, l.remarks,
                        l.loan_approval_time, l.processing_time,
                        l.minimum_loan_amount, l.maximum_loan_amount, l.minimum_credit_score
                FROM m
                JOIN public.lenders l ON l.id = m.lender_id
                ),
                incl AS (  -- what API returns (requires ROI)
                SELECT *
                FROM j
                WHERE home_loan_roi IS NOT NULL AND btrim(home_loan_roi) <> ''
                )
                SELECT
                (SELECT id FROM proj) AS project_id,
                (SELECT COUNT(*) FROM m) AS mapping_count,
                (SELECT COUNT(*) FROM incl) AS lenders_with_roi,
                json_agg(row_to_json(incl) ORDER BY lender_name) AS returned_lenders
                FROM incl;
            """, (canonical_property, canonical_property))
            row = cur.fetchone()

        # Optional: quick visibility to ensure we picked the right project
        if row:
            proj_id, map_cnt, with_roi_cnt, lenders_json = row
            print(f"[APF] PROJ={proj_id} map_cnt={map_cnt} with_roi={with_roi_cnt}")
            # Parse the JSON array of lenders back into dicts
            if lenders_json:
                for ld in lenders_json:
                    # ensure id is string and add numeric ROI for later sort if needed
                    if isinstance(ld.get("id"), uuid.UUID):
                        ld["id"] = str(ld["id"])
                    approved_lenders.append(ld)

        # If you still want to sort by numeric ROI:
        for l in approved_lenders:
            val = re.search(r'\d+(\.\d+)?', l.get("home_loan_roi") or "")
            l["_roi_num"] = float(val.group()) if val else None
        approved_lenders.sort(key=lambda x: (x["_roi_num"] is None, x["_roi_num"]))
        for l in approved_lenders:
            l.pop("_roi_num", None)

    except Exception as e:
        print(f"❌ Error fetching approved lenders (CTE): {e}")
        approved_lenders = []
    finally:
        if conn:
            conn.close()



    # --- 3) MERGE (APF first then CIBIL), dedupe by id and name ---
    merged = []
    seen_ids = set()
    seen_names = set()
    for src in (approved_lenders, lenders):
        for item in src:
            lid = item.get("id")
            lname = _norm(item.get("lender_name", ""))
            if lid and lid in seen_ids:
                continue
            if not lid and lname in seen_names:
                continue
            merged.append(item)
            if lid:
                seen_ids.add(lid)
            if lname:
                seen_names.add(lname)

    # --- 4) Build APPROVED (highest precedence) ---
    approved_ids = _idset(approved_lenders)
    approved_names = _nameset(approved_lenders)

    # --- 5) From remaining, pick WORKING (priority banks) ---
    buckets = {k: None for k in PRIORITY_ORDER}  # preserve order
    non_approved_pool = []
    for item in merged:
        lid = item.get("id")
        lname = _norm(item.get("lender_name", ""))
        if (lid and lid in approved_ids) or (lname and lname in approved_names):
            continue

        key = _priority_key(item.get("lender_name", ""))
        if key and buckets[key] is None:
            buckets[key] = item
        else:
            non_approved_pool.append(item)

    working_lenders = [buckets[k] for k in PRIORITY_ORDER if buckets[k] is not None]

    # --- 6) MORE LENDERS = merged minus (approved ∪ working) ---
    working_ids = _idset(working_lenders)
    working_names = _nameset(working_lenders)

    more_lenders = []
    for item in merged:
        lid = item.get("id")
        lname = _norm(item.get("lender_name", ""))
        if (lid and lid in approved_ids) or (lname and lname in approved_names):
            continue
        if (lid and lid in working_ids) or (lname and lname in working_names):
            continue
        more_lenders.append(item)

    # --- 7) Optional cap ---
    total_cap = 13
    if len(working_lenders) >= total_cap:
        more_lenders = []
    else:
        more_lenders = more_lenders[: total_cap - len(working_lenders)]

    # --- 8) Remove temporary float field before returning ---
    for lst in (approved_lenders, working_lenders, more_lenders):
        for l in lst:
            l.pop("home_loan_roi_float", None)

    # --- 9) Return clean response ---
    return {
        "message": "Lenders fetched successfully",
        "cibilScore": score,
        "approvedLenders": _clean_lenders(approved_lenders),
        "workingLenders": _clean_lenders(working_lenders),
        "moreLenders": _clean_lenders(more_lenders),
    }

TTL_DAYS = 30
async def intell_report_from_json(report: Dict) -> dict:
    """Accepts a bureau report as a dict, sends it as a JSON file to Orbit AI, and returns the JSON response."""
    if not isinstance(report, dict) or not report:
        raise HTTPException(status_code=400, detail="Body must be a non-empty JSON object")

    pan = report.get("pan_number")

    # --- Cache check with 30-day TTL ---
    if pan:
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT intell_report, created_at
                    FROM user_cibil_logs
                    WHERE pan = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (pan,),
                )
                row = cur.fetchone()
            conn.close()
        except Exception as e:
            print("⚠️ Cache lookup failed:", e)
            row = None
        if row:
            cached_blob, created_at = row
            # treat anything older than TTL as stale → regenerate
            cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
            # If your created_at is naive (no tz), assume UTC:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            if cached_blob and created_at >= cutoff:
                try:
                    return cached_blob if isinstance(cached_blob, dict) else json.loads(cached_blob)
                except Exception:
                    # Badly stored blob? fall through to regenerate
                    pass
            # else: stale or missing → regenerate


    data_bytes = json.dumps(report, ensure_ascii=False, default=str).encode("utf-8")
    files = {"file": ("report.json", io.BytesIO(data_bytes), "application/json")}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
        resp.raise_for_status()
        data = resp.json()
        pan = data.get("user_details").get("pan")

        #intell response logging
        try:
            # Check if intell_response is a dictionary and serialize it
            if isinstance(data, dict):
                serialized_intell_response = json.dumps(data)
                print("Serialized intell_response:", serialized_intell_response)  # Debugging

            # Insert the serialized response into the database
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE user_cibil_logs
                    SET intell_report = %s
                    WHERE pan = %s
                """, (
                    serialized_intell_response,  # Pass the serialized JSON
                    pan  # The pan number to identify the row to update
                ))
                conn.commit()

            conn.close()
            print("✅ Cibil log saved to database.")
        except Exception as log_err:
            print("❌ Error logging cibil data:", log_err)

        return data
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code,
                            detail=f"Orbit API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")
    
async def upsert_changes(conn, table: str, key_col: str, row: dict):
    """
    Compare payload with DB by `key_col`. Insert if not exists; else update only changed columns.
    Returns: {"inserted": bool, "updated": bool, "changed": {col: {"old": x, "new": y}, ...}}
    """
    key_val = row[key_col]
    existing = await conn.fetchrow(f"SELECT * FROM {table} WHERE {key_col} = $1", key_val)

    if not existing:
        cols = list(row.keys())
        vals = [row[c] for c in cols]
        placeholders = ", ".join(f"${i+1}" for i in range(len(vals)))
        col_clause = ", ".join(cols)
        await conn.execute(
            f"INSERT INTO {table} ({col_clause}) VALUES ({placeholders})",
            *vals,
        )
        return {"inserted": True, "updated": False, "changed": {c: {"old": None, "new": row[c]} for c in cols}}

    # compute diffs
    changed = {}
    for c, new_val in row.items():
        if c == key_col:
            continue
        old_val = existing[c]
        if old_val != new_val:
            changed[c] = {"old": old_val, "new": new_val}

    if not changed:
        return {"inserted": False, "updated": False, "changed": {}}

    # dynamic UPDATE only for changed columns
    set_cols = list(changed.keys())
    assignments = ", ".join(f"{c} = ${i+1}" for i, c in enumerate(set_cols))
    values = [row[c] for c in set_cols] + [key_val]
    await conn.execute(
        f"UPDATE {table} SET {assignments} WHERE {key_col} = ${len(values)}",
        *values,
    )
    return {"inserted": False, "updated": True, "changed": changed}


def mandate_consent_cibilscore(data: mandate_cibil):
    # body = {
    #     "MobileNumber": data.MobileNumber,
    #     "IsCustomerSelfJourney": data.IsCustomerSelfJourney
    # }
    # print(body)
    is_self = "true" if bool(data.IsCustomerSelfJourney) else "false"
    params = {"MobileNumber": data.MobileNumber, "IsCustomerSelfJourney": is_self}
    headers = get_signature_headers(basic_cibil, "POST", params)
    print(headers)
    response = requests.post(basic_cibil, headers=headers, params=params)
    print(response.url)
    api_data = response.json()

    print(api_data)

    return api_data


