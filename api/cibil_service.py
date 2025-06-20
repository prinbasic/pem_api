import json, requests
from datetime import datetime
from time import sleep
from models.request_models import LoanFormData
from api.log_utils import log_user_cibil_data
from api.signature import get_signature_headers
from models.request_models import CibilRequest
# from routes.utility_routes import calculate_emi
from db_client import get_db_connection  # make sure this is imported
import re
from fastapi.responses import JSONResponse
import uuid


API_1_URL = "https://dev-pemnew.basichomeloan.com/api/v1/CibilScore/InitiateCibilScoreRequest"
API_2_URL = "https://dev-pemnew.basichomeloan.com/api/v1/CibilScore/CustomerConsentOtpVerification"
API_3_URL = "https://dev-pemnew.basichomeloan.com/api/v1/CibilScore/GetCustomerConsentDataByTranId"
API_4_URL = "https://dev-pemnew.basichomeloan.com/api/v1/CibilScore/GetCreditScoreByPanApiUseOnly"

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

# def initiate_cibil_score(data: CibilRequest):
#     score = None
#     trans = None
#     report = None
#     print(data.hasCibil, data.cibilScore)
#     # 🔍 Case 1: User-provided CIBIL
#     if data.hasCibil == "yes" and data.cibilScore not in [None, 0, ""]:
#         score = data.cibilScore
#         print(f"✅ Using user-provided CIBIL score: {score}")
#     else:
#         score = data.cibilScore
#     if data.hasCibil == "no" and data.proceedScoreCheck == "no":
#         score = "750"
#     # 🔍 Case 2: Initiate Equifax CIBIL
#     body = {
#         "panNumber": data.panNumber,
#         "mobileNumber": data.mobileNumber,
#         "firstName": data.firstName,
#         "lastName": data.lastName,
#         "emailAddress": data.emailAddress,
#         "dob": datetime.strptime(data.dob, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00"),
#         "gender": data.gender,
#         "pinCode": data.pinCode,
#         "applicationId": str(data.applicationId) if data.applicationId else None
#     }

#     headers = get_signature_headers(API_1_URL, "POST", body)
#     response = requests.post(API_1_URL, headers=headers, json=body)
#     api_data = response.json()
#     print(api_data)

#     result = api_data.get("result", {})
#     print(result)
#     score = result.get("cibilScore")
#     if score is True:
#         trans = result.get("transID")
#         print(trans)
#         if trans:
#             cibil_request_cache[trans] = data

#         if not score:
#             return {
#                 "message": "OTP sent to customer.",
#                 "transId": trans,
#                 "cibilScore": None,
#                 "status": "otp_required"
#             }

#         # 🔗 Fetch Equifax report if Equifax was used
#         report = fetch_equifax_report_by_pan(data.panNumber)
    

#     # 🔍 Now we have a score (user-provided or Equifax); fetch lenders and calculate EMI
#     try:
#         conn = get_db_connection()
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
#                     home_loan_ltv, remarks, loan_approval_time, processing_time,
#                     minimum_loan_amount, maximum_loan_amount
#                 FROM lenders
#                 WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
#                 AND home_loan_roi IS NOT NULL
#                 AND home_loan_roi != ''
#                 ORDER BY CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
#             """, (score,))
#             rows = cur.fetchall()
#             col_names = [desc[0] for desc in cur.description]
#         conn.close()
#         lenders = [dict(zip(col_names, row)) for row in rows]
#     except Exception as e:
#         print("❌ Error fetching lenders:", e)
#         lenders = []

#     # 🏠 Handle propertyName if provided
#     property_name = getattr(data, "propertyName", None)
#     approved_lenders = []
#     if property_name:
#         try:
#             conn = get_db_connection()
#             with conn.cursor() as cur:
#                 cur.execute("""
#                     SELECT l.id, l.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
#                         l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
#                         l.minimum_loan_amount, l.maximum_loan_amount
#                     FROM approved_projects ap
#                     JOIN approved_projects_lenders apl ON apl.project_id = ap.id
#                     JOIN lenders l ON l.id = apl.lender_id
#                     WHERE LOWER(ap.project_name) LIKE LOWER(%s)
#                 """, (f"%{property_name}%",))
#                 rows = cur.fetchall()
#                 col_names = [desc[0] for desc in cur.description]
#                 approved_lenders = [dict(zip(col_names, row)) for row in rows]
#             conn.close()
#         except Exception as e:
#             print("❌ Error fetching approved lenders:", e)

#     approved_ids = {l['id'] for l in approved_lenders}
#     remaining_lenders = [l for l in lenders if l.get('id') not in approved_ids]
#     combined_lenders = approved_lenders + remaining_lenders
#     limited_lenders = combined_lenders[:9]

#     # 🚀 Prepare LoanFormData for logging
#     form_data = LoanFormData(
#         name=f"{data.firstName} {data.lastName}".strip(),
#         email=data.emailAddress,
#         pan=data.panNumber,
#         dob=data.dob,
#         phone=data.mobileNumber,
#         profession=data.profession,
#         loanAmount=data.loanAmount,
#         tenureYears=data.tenureYears,
#         location=data.pinCode,
#         hasCibil=data.hasCibil or "no",
#         cibilScore=score,
#         proceedScoreCheck=data.proceedScoreCheck,
#         gender=data.gender,
#         pin=data.pinCode,
#         propertyName=property_name
#     )

#     # 💰 EMI Calculation
#     emi_data = []
#     for lender in limited_lenders:
#         roi = lender.get("home_loan_roi")
#         if roi and roi.strip():
#             emi = calculate_emi_amount(
#                 loan_amount=form_data.loanAmount,
#                 roi_string=roi,
#                 years=form_data.tenureYears
#             )
#             emi_value = emi if emi else "Data Not Available"
#         else:
#             emi_value = "Data Not Available"

#         emi_data.append({
#             "lender": lender.get("lender_name"),
#             "emi": emi_value,
#             "lender_type": lender.get("lender_type"),
#             "remarks": lender.get("remarks", "Data Not Available"),
#             "home_loan_ltv": lender.get("home_loan_ltv", "Data Not Available"),
#             "loan_approval_time": lender.get("loan_approval_time", "Data Not Available"),
#             "processing_time": lender.get("processing_time", "Data Not Available"),
#             "min_loan_amount": lender.get("minimum_loan_amount", "Data Not Available"),
#             "max_loan_amount": lender.get("maximum_loan_amount", "Data Not Available")
#         })

#     # Clean IDs for response
#     def clean_lenders(lenders_list):
#         for lender in lenders_list:
#             lender.pop('id', None)
#         return lenders_list

#     approved_lenders = clean_lenders(approved_lenders)
#     remaining_lenders = clean_lenders(remaining_lenders)

#     # ✅ Log data
#     log_user_cibil_data(form_data, {
#         "cibilScore": score,
#         "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
#         "topMatches": lenders[:3],
#         "moreLenders": lenders[3:9]
#     }, emi_data)

#     return {
#     "message": "CIBIL score available. Report and lenders fetched.",
#     "cibilScore": score,
#     "transId": trans,
#     "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
#     "approvedLenders": approved_lenders,
#     "moreLenders": remaining_lenders,
#     "emi_data": emi_data
# }

def initiate_cibil_score(data: CibilRequest):
    score = None
    trans = None
    report = None

    # 🔍 Case 1: Use user-provided CIBIL score if available
    if data.hasCibil == "yes" and data.cibilScore not in [None, 0, ""]:
        score = data.cibilScore
        print(f"✅ Using user-provided CIBIL score: {score}")
    elif data.hasCibil == "no" and data.proceedScoreCheck == "no":
        # Default score for cases where no score is provided and the user doesn't want to proceed
        score = 750  # Default score if user denies CIBIL score check
        print(f"✅ Using default CIBIL score: {score}")
    else:
        # 🔍 Case 2: Initiate Equifax CIBIL fetch
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

        # Send request to external CIBIL service (Equifax)
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

        # Handle case when CIBIL score is not found in the response
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

    # 🔍 Fetch lenders based on CIBIL score (whether user-provided or from Equifax)
    lenders = []
    # try:
    #     conn = get_db_connection()
    #     with conn.cursor() as cur:
    #         cur.execute("""
    #             SELECT id, lender_name, lender_type, home_loan_roi, lap_roi,
    #                 home_loan_ltv, remarks, loan_approval_time, processing_time,
    #                 minimum_loan_amount, maximum_loan_amount
    #             FROM lenders
    #             WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
    #             AND home_loan_roi IS NOT NULL
    #             AND home_loan_roi != ''
    #             ORDER BY CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
    #         """, (score,))
    #         rows = cur.fetchall()
    #         col_names = [desc[0] for desc in cur.description]
    #     conn.close()
    #     lenders = [dict(zip(col_names, row)) for row in rows]
    # except Exception as e:
    #     print("❌ Error fetching lenders:", e)

    # # 🏠 Handle propertyName if provided
    # property_name = getattr(data, "propertyName", None)
    # approved_lenders = []
    # if property_name:
    #     try:
    #         conn = get_db_connection()
    #         with conn.cursor() as cur:
    #             cur.execute("""
    #                 SELECT l.idl.lender_name, l.lender_type, l.home_loan_roi, l.lap_roi,
    #                     l.home_loan_ltv, l.remarks, l.loan_approval_time, l.processing_time,
    #                     l.minimum_loan_amount, l.maximum_loan_amount
    #                 FROM approved_projects ap
    #                 JOIN approved_projects_lenders apl ON apl.project_id = ap.id
    #                 JOIN lenders l ON l.id = apl.lender_id
    #                 WHERE LOWER(ap.project_name) LIKE LOWER(%s)
    #             """, (f"%{property_name}%",))
    #             rows = cur.fetchall()
    #             col_names = [desc[0] for desc in cur.description]
    #             approved_lenders = [dict(zip(col_names, row)) for row in rows]
    #         conn.close()
    #     except Exception as e:
    #         print("❌ Error fetching approved lenders:", e)

    # # Filter out lenders who are already approved for the project
    # approved_ids = {l['id'] for l in approved_lenders}
    # remaining_lenders = [l for l in lenders if l.get('id') not in approved_ids]
    # combined_lenders = approved_lenders + remaining_lenders
    # limited_lenders = combined_lenders[:9]

    # # 🚀 Prepare LoanFormData for logging
    # form_data = LoanFormData(
    #     name=f"{data.firstName} {data.lastName}".strip(),
    #     email=data.emailAddress,
    #     pan=data.panNumber,
    #     dob=data.dob,
    #     phone=data.mobileNumber,
    #     profession=data.profession,
    #     loanAmount=data.loanAmount,
    #     tenureYears=data.tenureYears,
    #     location=data.pinCode,
    #     hasCibil=data.hasCibil or "no",
    #     cibilScore=score,
    #     proceedScoreCheck=data.proceedScoreCheck,
    #     gender=data.gender,
    #     pin=data.pinCode,
    #     propertyName=property_name
    # )

    # # 💰 EMI Calculation for lenders
    # emi_data = []
    # for lender in limited_lenders:
    #     roi = lender.get("home_loan_roi")
    #     if roi and roi.strip():
    #         emi = calculate_emi_amount(
    #             loan_amount=form_data.loanAmount,
    #             roi_string=roi,
    #             years=form_data.tenureYears
    #         )
    #         emi_value = emi if emi else "Data Not Available"
    #     else:
    #         emi_value = "Data Not Available"

    #     emi_data.append({
    #         "lender": lender.get("lender_name"),
    #         "emi": emi_value,
    #         "lender_type": lender.get("lender_type"),
    #         "remarks": lender.get("remarks", "Data Not Available"),
    #         "home_loan_ltv": lender.get("home_loan_ltv", "Data Not Available"),
    #         "loan_approval_time": lender.get("loan_approval_time", "Data Not Available"),
    #         "processing_time": lender.get("processing_time", "Data Not Available"),
    #         "min_loan_amount": lender.get("minimum_loan_amount", "Data Not Available"),
    #         "max_loan_amount": lender.get("maximum_loan_amount", "Data Not Available")
    #     })

    # # Clean lender IDs for response
    # def clean_lenders(lenders_list):
    #     for lender in lenders_list:
    #         lender.pop('id', None)
    #     return lenders_list

    # approved_lenders = clean_lenders(approved_lenders)
    # remaining_lenders = clean_lenders(remaining_lenders)

    # # ✅ Log data
    # log_user_cibil_data(form_data, {
    #     "cibilScore": score,
    #     "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
    #     "topMatches": lenders[:3],
    #     "moreLenders": lenders[3:9]
    # }, emi_data)

    # # Return the final response
    # return {
    #     "message": "CIBIL score available. Report and lenders fetched.",
    #     "cibilScore": score,
    #     "transId": trans,
    #     "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
    #     "approvedLenders": approved_lenders,
    #     "moreLenders": remaining_lenders,
    #     "emi_data": emi_data,
    #     "data":data
    # }
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
        hasCibil=data.hasCibil or "no",
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

    return {
        "message": "CIBIL score available. Report and lenders fetched.",
        "cibilScore": score,
        "transId": trans,
        "raw": report.get("raw") if report else "User-provided score; Equifax skipped",
        "approvedLenders": approved_lenders,
        "moreLenders": remaining_lenders,
        "emi_data": emi_data,
        "data": data
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

def poll_consent_and_fetch(trans_id: str, pan: str, original_request: CibilRequest, attempts=5, wait=15):
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
            "equifaxScore": report_json.get("result").get("customerCreditScore"),
            "raw": final_data  # ✅ this ensures everything is saved/logged
        }

    except Exception as e:
        print(f"❌ Exception in API 4 call: {e}")
        return {
            "error": str(e),
            "raw": None
        }

# def fetch_equifax_report_by_pan(pan_number: str):
#     try:
#         # Step 1: Construct full API URL
#         full_url = f"{API_4_URL}?PanNumber={pan_number}&includeReportJson=true"
#         print(f"\n🔗 API 4 URL: {full_url}")

#         # Step 2: Sign request
#         headers = get_signature_headers(full_url.lower(), "GET", None)

#         # Step 3: Make the API call
#         response = requests.get(full_url, headers=headers)
#         final_data = response.json()
#         print(final_data)

#         report_json = final_data.get("result", {}).get("equifaxReportJson", {})
#         score = report_json.get("customerCreditScore")

#         if score:
#             # Optionally fetch lenders based on score
#             lenders = []
#             emi_data = []

#             try:
#                 conn = get_db_connection()
#                 with conn.cursor() as cur:
#                     cur.execute("""
#                         SELECT lender_name, lender_type, home_loan_roi, lap_roi,
#                                home_loan_ltv, remarks, loan_approval_time, processing_time,
#                                minimum_loan_amount, maximum_loan_amount
#                         FROM lenders
#                         WHERE CAST(LEFT(minimum_credit_score, 3) AS INTEGER) <= %s
#                           AND home_loan_roi IS NOT NULL
#                           AND home_loan_roi != ''
#                         ORDER BY 
#                             CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
#                     """, (score,))
#                     rows = cur.fetchall()
#                     col_names = [desc[0] for desc in cur.description]
#                 conn.close()

#                 lenders = [dict(zip(col_names, row)) for row in rows]

#                 # Calculate EMI for each lender
#                 for lender in lenders:
#                     roi = lender.get("home_loan_roi")
#                     if roi:
#                         emi = calculate_emi_amount(loan_amount=, roi_string=roi)
#                         if emi:
#                             lender["emi"] = emi
#                             emi_data.append({ "lender": lender["lender_name"], "emi": emi })

#                 print("emi calculated entered if block")
#             except Exception as e:
#                 print("❌ Error fetching lenders or calculating EMI:", e)

#             return {
#                 "message": "CIBIL score fetched using PAN. Report and lenders returned.",
#                 "cibilScore": score,
#                 "report": report_json,
#                 "raw": final_data,
#                 "topMatches": lenders[:3],
#                 "moreLenders": lenders[3:9],
#                 "emi_data": emi_data
#             }

#         else:
#             return {
#                 "message": "Score not found in report.",
#                 "status": "no_score",
#                 "raw": final_data
#             }

#     except Exception as e:
#         print(f"❌ Exception in API 4 call: {e}")
#         return {
#             "message": "Failed to fetch report.",
#             "status": "error",
#             "error": str(e),
#             "raw": None
#         }
