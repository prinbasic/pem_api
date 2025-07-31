import json, requests
from datetime import datetime
from fastapi import HTTPException, Body
from time import sleep
from models.request_models import LoanFormData
from api.log_utils import log_user_cibil_data
from api.signature import get_signature_headers
from models.request_models import cibilRequest
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

load_dotenv()

API_1_URL = os.getenv("API_1_URL")
API_2_URL = os.getenv("API_2_URL")
API_3_URL = os.getenv("API_3_URL")
API_4_URL = os.getenv("API_4_URL")
GRIDLINES_PAN_URL = os.getenv("GRIDLINES_PAN_URL")
GRIDLINES_API_KEY = os.getenv("GRIDLINES_API_KEY")
OTP_BASE_URL = os.getenv("OTP_BASE_URL")
BUREAU_PROFILE_URL = os.getenv("BUREAU_PROFILE_URL")

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
                    JOIN approved_projects_lenders apl ON apl.project_id = ap.id
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
                resp = requests.post("https://dev-api.orbit.basichomeloan.com/ai/generate_cibil_report", files=files)
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
#                         resp = requests.post("https://dev-api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
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
            approved_lenders = []
            remaining_lenders = []
            emi_data = {}
            data = bureau_json.get("data")
            raw_report_data = None


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
                        pan_data.get("data", {}).get("pan_data", {}).get("document_id"),
                        pan_data.get("data", {}).get("pan_data", {}).get("date_of_birth"),
                        pan_data.get("data", {}).get("pan_data", {}).get("name"),
                        phone_number,
                        pan_data.get("data", {}).get("pan_data", {}).get("address_data", {}).get("pincode"),
                        pan_data.get("data", {}).get("pan_data", {}).get("email", None),
                        json.dumps(raw),
                        score,
                        datetime.now(timezone.utc),
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
            #             resp = requests.post("https://dev-api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
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
            

            return {
                "message": "Credit score available. Report and lenders fetched.",
                "cibilScore": score,
                "transId": trans,
                "raw": raw,
                "approvedLenders": approved_lenders,
                "moreLenders": remaining_lenders,
                "emi_data": emi_data,
                "data": data,
                # "intell_response": intell_response
            }

        except Exception as e:
            print("❌ Exception occurred while verifying PAN/Bureau:")
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
                SELECT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
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

