from fastapi import HTTPException
import random
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
import string
import httpx
import tempfile
import traceback 
import json
import requests
from db_client import get_db_connection  # make sure this is imported
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
from api.cibil_service import send_and_verify_pan

load_dotenv()

OTP_BASE_URL = os.getenv("OTP_BASE_URL")
MOBILE_TO_PAN_URL = os.getenv("MOBILE_TO_PAN_URL")
MOBILE_TO_PREFILL_URL = os.getenv("MOBILE_TO_PREFILL_URL")
PAN_SUPREME_URL = os.getenv("PAN_SUPREME_URL")
# PAN_SUPREME_URL = os.getenv("PAN_SUPREME_URL")
CIBIL_URL = os.getenv("CIBIL_URL")

API_KEY = os.getenv("API_KEY")
HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json; charset=utf-8"
}

STATE_CODE_MAPPING = {
    "Jammu & Kashmir": 1,
    "Himachal Pradesh": 2,
    "Punjab": 3,
    "Chandigarh": 4,
    "Uttarakhand": 5,
    "Haryana": 6,
    "Delhi": 7,
    "Rajasthan": 8,
    "Uttar Pradesh": 9,
    "Bihar": 10,
    "Sikkim": 11,
    "Arunachal Pradesh": 12,
    "Nagaland": 13,
    "Manipur": 14,
    "Mizoram": 15,
    "Tripura": 16,
    "Meghalaya": 17,
    "Assam": 18,
    "West Bengal": 19,
    "Jharkhand": 20,
    "Odisha": 21,
    "Chhattisgarh": 22,
    "Madhya Pradesh": 23,
    "Gujarat": 24,
    "Daman and Diu": 25,  # pre-2020
    "Dadra & Nagar Haveli and Daman & Diu": 26,  # post-2020
    "Maharashtra": 27,
    "Andhra Pradesh": 28,  # pre-bifurcation
    "Karnataka": 29,
    "Goa": 30,
    "Lakshadweep": 31,
    "Kerala": 32,
    "Tamil Nadu": 33,
    "Puducherry": 34,
    "Andaman & Nicobar Islands": 35,
    "Telangana": 36,
    "Andhra Pradesh (new)": 37,
    "Ladakh": 38,
    "Other Territory": 97,
    "Centre / Central Jurisdiction": 99
}


def generate_ref_num(prefix="BBA"):
    timestamp = datetime.now().strftime("%d%m%y")
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
    return f"{prefix}{timestamp}{suffix}"


STATUS_MEANING = {
    "0": "Current / no DPD",
    "1": "30+ DPD",
    "2": "60+ DPD",
    "3": "90+ DPD",
    "STD": "Standard / performing",
    "SMA": "Special Mention Account",
    "SUB": "Sub-standard",
    "DBT": "Doubtful",
    "LSS": "Loss",
    "XXX": "No data reported",
}

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse dates like '2025-08-05+05:30' or '2025-08-05' to aware UTC datetime."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        # datetime.fromisoformat handles offsets like +05:30
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            # fallback: strip offset if present
            base = dt_str.split("+")[0]
            return datetime.strptime(base, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None

def _to_num(val: Any) -> Optional[float]:
    """Convert numeric strings like '-1', '14481', '3,48,334' to float; treat '-1' as None."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val >= 0 else None
    if isinstance(val, str):
        s = val.strip().replace(",", "")
        try:
            f = float(s)
            return f if f >= 0 else None
        except Exception:
            return None
    return None

def _iter_tradelines(obj: Any):
    """
    Recursively traverse the JSON to yield tradeline-like dicts.
    We recognize entries that either:
      - have a 'Tradeline' key (wrapper objects), or
      - look like a tradeline (have GrantedTrade & creditorName).
    """
    if isinstance(obj, dict):
        if "Tradeline" in obj and isinstance(obj["Tradeline"], dict):
            yield obj["Tradeline"]
        else:
            # direct shape
            if "GrantedTrade" in obj and "creditorName" in obj:
                yield obj
        for v in obj.values():
            yield from _iter_tradelines(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _iter_tradelines(it)

def extract_latest_emi_last_n_days(cibil_data: Dict[str, Any], days: int = 30) -> List[Dict[str, Any]]:
    """
    Extract per-tradeline latest EMI/repayment signal within the last `days`.
    Looks at MonthlyPayStatus dates and dateLastPayment.
    """
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)
    out: List[Dict[str, Any]] = []

    for tl in _iter_tradelines(cibil_data):
        gt = tl.get("GrantedTrade", {}) if isinstance(tl, dict) else {}
        ph = gt.get("PayStatusHistory", {}) if isinstance(gt, dict) else {}
        mps = ph.get("MonthlyPayStatus", [])
        if isinstance(mps, dict):
            # sometimes API returns single object
            mps = [mps]

        # find latest MonthlyPayStatus within window
        latest_mps_rec: Optional[Dict[str, Any]] = None
        for rec in mps:
            dt = _parse_dt(rec.get("date"))
            if dt and dt >= cutoff and dt <= now_utc:
                if (latest_mps_rec is None) or (_parse_dt(latest_mps_rec.get("date")) or datetime.min.replace(tzinfo=timezone.utc)) < dt:
                    latest_mps_rec = rec

        # last payment check
        dlp_dt = _parse_dt(gt.get("dateLastPayment"))
        dlp_in_window = dlp_dt is not None and cutoff <= dlp_dt <= now_utc

        # include tradeline if there is any signal in window
        if latest_mps_rec is None and not dlp_in_window:
            continue

        status_raw = latest_mps_rec.get("status") if latest_mps_rec else None
        emi_amount = _to_num(gt.get("EMIAmount"))
        actual_payment_amount = _to_num(gt.get("actualPaymentAmount"))
        current_balance = _to_num(tl.get("currentBalance"))

        out.append({
            "creditorName": tl.get("creditorName"),
            "accountTypeSymbol": tl.get("AccountType", {}).get("symbol") or tl.get("CreditType", {}).get("symbol") or tl.get("accountTypeSymbol"),
            "accountNumber": tl.get("accountNumber"),
            "emi_amount": emi_amount,
            "last_emi_month": latest_mps_rec.get("date") if latest_mps_rec else None,
            "monthly_status_raw": status_raw,
            "monthly_status_meaning": STATUS_MEANING.get(status_raw, "Unknown") if status_raw else None,
            "dateLastPayment": gt.get("dateLastPayment"),
            "last_payment_amount": actual_payment_amount,
            "currentBalance": current_balance,
            "open_or_closed": ("Closed" if tl.get("currentBalance") in ("0", 0) or tl.get("dateClosed") else "Open"),
        })

    # Sort most recent first by max(last_emi_month, dateLastPayment)
    def _key(rec):
        a = _parse_dt(rec.get("last_emi_month"))
        b = _parse_dt(rec.get("dateLastPayment"))
        return max([d for d in (a, b) if d is not None] or [datetime.min.replace(tzinfo=timezone.utc)], key=lambda x: x)

    out.sort(key=_key, reverse=True)
    return out


async def trans_bank_fetch_flow(phone_number: str) -> dict:
    final_pan_number = None
    try:
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
            print(f"mobile to prefill response", mobile_to_prefill_resp)
            try:
                mobile_to_prefill_data = mobile_to_prefill_resp.json()
            except Exception as e:
                print(f"❌ Failed to parse mobile_to_prefill_resp JSON: {e}")
                print(f"❌ Raw Response Content: {mobile_to_prefill_resp.content}")
            print("📋 Full Mobile to Prefill API Response:", mobile_to_prefill_data)

            # Check if message is "no record found"
            message = mobile_to_prefill_data.get("message", "").lower()
            if message == "no record found":
                raise HTTPException(
                    status_code=404,
                    detail="No record found for the given mobile number."
                )

            # Fallback: Check if result contains PAN
            result = mobile_to_prefill_data.get("result")
            if not result or not isinstance(result, dict) or not result.get("pan"):
                raise HTTPException(
                    status_code=400,
                    detail=f"PAN number not returned in Mobile to Prefill response. Raw response: {mobile_to_prefill_data}"
                )

            # if not mobile_to_prefill_data.get("result") or not mobile_to_prefill_data["

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

            state_name = pan_details.get("address", {}).get("state", "").strip()
            print(state_name)

            region_code_int = STATE_CODE_MAPPING.get(state_name, 97)  # default to 97 for "Other Territory"
            region_code = f"{region_code_int:02}"  # zero-pad if less than 10

            print(region_code)

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
                            # "StreetAddress": pan_details["address"].get("address_line_1", "").strip(),
                            # "City": pan_details["address"].get("address_line_5", "").strip(),  # BOKARO
                            # "PostalCode": int(pan_details["address"].get("pin_code", 0)),
                            # "Region": region_code,
                            # "AddressType": 1
                            "StreetAddress": "plot no. 266/c",
                            "City": "BOKARO",  # BOKARO
                            "PostalCode": int(pan_details["address"].get("pin_code", 0)),
                            "Region": region_code,
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

            print(f"cibil data : {cibil_data}")

            try:
                borrower = (
                    cibil_data.get("cibilData", {})
                    .get("GetCustomerAssetsResponse", {})
                    .get("GetCustomerAssetsSuccess", {})
                    .get("Asset", {})
                    .get("TrueLinkCreditReport", {})
                    .get("Borrower", {})
                )

                dob_raw = borrower.get("Birth", {}).get("date", "")
                dob_clean = dob_raw.split("+")[0] if "+" in dob_raw else dob_raw

                # Convert to dd-mm-yyyy format
                dob_formatted = ""
                if dob_clean:
                    try:
                        dob_obj = datetime.strptime(dob_clean, "%Y-%m-%d")
                        dob_formatted = dob_obj.strftime("%d-%m-%Y")
                    except ValueError:
                        dob_formatted = dob_clean  # fallback in case parsing fails

                email_data = borrower.get("EmailAddress", {})
                if isinstance(email_data, dict):  # Case when EmailAddress is a dict
                    email = email_data.get("Email", "")
                elif isinstance(email_data, list) and len(email_data) > 0:  # Case when it's a list
                    email = email_data[0].get("Email", "")
                else:
                    email = ""

                print("................................................................................................ userdetail")

                dob = dob_formatted
                print("DOB:", dob)

                credit_score = borrower.get("CreditScore", {}).get("riskScore")
                print("Credit Score:", credit_score)

                email = email
                print("Email:", email)

                gender = borrower.get("Gender", "")
                print("Gender:", gender)

                pan_number = borrower.get("IdentifierPartition", {}).get("Identifier", [{}])[1].get("ID", {}).get("Id", "")
                print("PAN Number:", pan_number)

                pincode = borrower.get("BorrowerAddress", [{}])[0].get("CreditAddress", {}).get("PostalCode", "")
                print("Pincode:", pincode)

                name = borrower.get("BorrowerName", {}).get("Name", {}).get("Forename") +" " + borrower.get("BorrowerName", {}).get("Name", {}).get("Surname")
                print("Name:", name)

                phone = phone_number
                print("Phone Number:", phone)

                user_details = {
                    "dob": dob_formatted,
                    "credit_score": credit_score,
                    "email": email,
                    "gender": gender,
                    "pan_number": pan_number,
                    "pincode": pincode,
                    "name": name,
                    "phone": phone_number
                }

                print(user_details)

            except Exception as e:
                print(f"❌ Error extracting user details: {e}")
                raise HTTPException(status_code=500, detail=f"CIBIL extraction failed: {str(e)}")

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
                        json.dumps(cibil_data),
                        user_details.get("credit_score"),
                        datetime.now(timezone.utc).isoformat()
                    ))
                    conn.commit()
                conn.close()
                print("✅ Cibil log saved to database.")
            except Exception as log_err:
                print("❌ Error logging cibil data:", log_err)


            # AI-generated report
            # def intell_report():
            #     try:
            #         if cibil_data.get("status") == "500":
            #             return {"error": "No raw cibil data found"}

            #         with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as tmpfile:
            #             json.dump(cibil_data, tmpfile)
            #             tmpfile.flush()
            #             tmpfile_path = tmpfile.name
            #         with open(tmpfile_path, 'rb') as f:
            #                 files = {'file': f}
            #                 resp = requests.post("https://dev-api.orbit.basichomeloan.com/ai/generate_credit_report", files=files)
            #                 resp.raise_for_status()
            #                 return resp.json()

            #     except Exception as e:
            #         return {"error": f"Intelligence report generation failed: {str(e)}"}

            # intell_response = intell_report()
            # # Check if intell_response is a dictionary and serialize it
            # if isinstance(intell_response, dict):
            #     serialized_intell_response = json.dumps(intell_response)
            #     print("Serialized intell_response:", serialized_intell_response)  # Debugging
            # try:
            #     # Insert the serialized response into the database
            #     conn = get_db_connection()
            #     with conn.cursor() as cur:
            #         cur.execute("""
            #             UPDATE user_cibil_logs
            #             SET intell_report = %s
            #             WHERE pan = %s
            #         """, (
            #             serialized_intell_response,  # Pass the serialized JSON
            #             final_pan_number  # The pan number to identify the row to update
            #         ))
            #         conn.commit()

            #     conn.close()
            #     print("✅ Cibil log saved to database.")
            # except Exception as log_err:
            #     print("❌ Error logging cibil data:", log_err)
            latest_emi_30d = extract_latest_emi_last_n_days(cibil_data, days=30)
            return {
                "pan_number": final_pan_number,
                "pan_supreme": pan_supreme_data,
                "cibil_report": cibil_data,
                # "intell_report": intell_response
                "profile_detail": user_details,
                "source": "cibil",
                "emi_data": latest_emi_30d
            }
    except Exception as e:
        print(f"⚠️ TransBank failed: {str(e)}. Trying fallback via Ongrid...")

        try:
            # PAN already fetched from earlier step
            if not final_pan_number:
                raise HTTPException(status_code=400, detail="PAN not available for fallback.")
            
            # Fake OTP to skip verification
            dummy_verified_otp = "NA"

            # Call fallback function with PAN + phone, and dummy OTP
            fallback_result = await send_and_verify_pan(
                phone_number=phone_number,
                otp=dummy_verified_otp,   # skipped inside logic
                pan_number=final_pan_number
            )

            return {
                "fallback_used": True,
                **fallback_result
            }

        except Exception as fallback_error:
            print("❌ Ongrid fallback also failed:", fallback_error)
            raise HTTPException(status_code=200, detail=f"Both TransBank and Ongrid failed: {str(fallback_error)}")

# async def trans_bank_fetch_flow(phone_number: str, pan_number: Optional[str] = None) -> dict:
#     try:
#         async with httpx.AsyncClient(timeout=60.0) as client:
#             if not phone_number:
#                 raise HTTPException(status_code=400, detail="Phone number is required")

#             # STEP 1: If PAN not provided, fetch it via Mobile-to-Prefill API
#             if not pan_number:
#                 print("📌 Fetching PAN using Mobile to Prefill API with name_lookup: 1.")
#                 client_ref_num = generate_ref_num()

#                 mobile_to_prefill_payload = {
#                     "client_ref_num": client_ref_num,
#                     "mobile_no": phone_number,
#                     "name_lookup": 1
#                 }

#                 mobile_to_prefill_resp = await client.post(
#                     MOBILE_TO_PREFILL_URL,
#                     headers=HEADERS,
#                     json=mobile_to_prefill_payload
#                 )

#                 print(f"🔍 Mobile to Prefill API Response Status [{mobile_to_prefill_resp.status_code}]")
#                 mobile_to_prefill_data = mobile_to_prefill_resp.json()
#                 print("📋 Full Mobile to Prefill API Response:", mobile_to_prefill_data)

#                 # Check message
#                 message = mobile_to_prefill_data.get("message", "").lower()
#                 if message == "no record found":
#                     print("❌ Mobile to Prefill API Error:", mobile_to_prefill_data)
#                     return {
#                         "status": "error",
#                         "message": "No record found for the given mobile number.",
#                         "raw_response": mobile_to_prefill_data
#                     }

#                 result = mobile_to_prefill_data.get("result")
#                 if not result or not isinstance(result, dict) or not result.get("pan"):
#                     return {
#                         "status": "error",
#                         "message": "PAN number not found in response.",
#                         "raw_response": mobile_to_prefill_data
#                     }

#                 pan_number = result["pan"]
#                 print(f"✅ Extracted PAN number: {pan_number}")
#             else:
#                 print(f"✅ Using provided PAN: {pan_number}")

#             # STEP 2: PAN Supreme Verification
#             pan_supreme_resp = await client.post(
#                 PAN_SUPREME_URL,
#                 headers=HEADERS,
#                 json={"pan": pan_number}
#             )
#             pan_supreme_data = pan_supreme_resp.json()
#             print(f"🔍 PAN Supreme API Response: {pan_supreme_data}")

#             if pan_supreme_data.get("status") != "1":
#                 return {
#                     "status": "error",
#                     "message": f"PAN Supreme verification failed: {pan_supreme_data.get('message', 'Unknown error')}",
#                     "pan": pan_number
#                 }

#             pan_details = pan_supreme_data["result"]
#             print(f"✅ PAN Supreme Details: {pan_details}")

#             # STEP 3: Prepare CIBIL payload
#             try:
#                 cibil_payload = {
#                     "CustomerInfo": {
#                         "Name": {
#                             "Forename": pan_details.get("first_name", "").strip(),
#                             "Surname": pan_details.get("last_name", "").strip()
#                         },
#                         "IdentificationNumber": {
#                             "IdentifierName": "TaxId",
#                             "Id": pan_number.strip()
#                         },
#                         "Address": {
#                             "StreetAddress": pan_details["address"].get("address_line_1", "").strip(),
#                             "City": pan_details["address"].get("address_line_5", "").strip(),
#                             "PostalCode": int(pan_details["address"].get("pin_code", 0)),
#                             "Region": 20,
#                             "AddressType": 1
#                         },
#                         "EmailID": pan_details.get("email", "").strip(),
#                         "DateOfBirth": pan_details.get("dob", "").strip(),
#                         "PhoneNumber": {
#                             "Number": int(phone_number)
#                         },
#                         "Gender": "Male" if pan_details["gender"].upper() == "M" else "Female"
#                     },
#                     "LegalCopyStatus": "Accept",
#                     "UserConsentForDataSharing": True
#                 }
#                 print("📋 CIBIL Payload:", cibil_payload)
#             except Exception as e:
#                 return {
#                     "status": "error",
#                     "message": f"Failed to construct CIBIL payload: {str(e)}"
#                 }

#             # STEP 4: Fetch CIBIL report
#             cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)
#             cibil_data = cibil_resp.json()

#             # STEP 5: Parse response
#             try:
#                 borrower = (
#                     cibil_data.get("cibilData", {})
#                     .get("GetCustomerAssetsResponse", {})
#                     .get("GetCustomerAssetsSuccess", {})
#                     .get("Asset", {})
#                     .get("TrueLinkCreditReport", {})
#                     .get("Borrower", {})
#                 )

#                 dob_raw = borrower.get("Birth", {}).get("date", "")
#                 dob_clean = dob_raw.split("+")[0] if "+" in dob_raw else dob_raw

#                 dob_formatted = ""
#                 if dob_clean:
#                     try:
#                         dob_obj = datetime.strptime(dob_clean, "%Y-%m-%d")
#                         dob_formatted = dob_obj.strftime("%d-%m-%Y")
#                     except ValueError:
#                         dob_formatted = dob_clean

#                 user_details = {
#                     "dob": dob_formatted,
#                     "credit_score": borrower.get("CreditScore", {}).get("riskScore"),
#                     "email": borrower.get("EmailAddress", [{}])[0].get("Email"),
#                     "gender": borrower.get("Gender"),
#                     "pan_number": borrower.get("IdentifierPartition", {}).get("Identifier", [{}])[1].get("ID", {}).get("Id"),
#                     "pincode": borrower.get("BorrowerAddress", [{}])[0].get("CreditAddress", {}).get("PostalCode"),
#                     "name": borrower.get("BorrowerName", {}).get("Name", {}).get("Forename"),
#                     "phone": phone_number
#                 }

#                 print(f"✅ User Details Extracted: {user_details}")
#             except Exception as e:
#                 print("❌ Error extracting user details:", str(e))
#                 user_details = {}

#             # STEP 6: Save to DB
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
#                         pan_number,
#                         pan_details.get("dob"),
#                         f"{pan_details.get('first_name', '')} {pan_details.get('last_name', '')}".strip(),
#                         phone_number,
#                         pan_details.get("address", {}).get("pin_code"),
#                         pan_details.get("email", None),
#                         json.dumps(cibil_data),
#                         user_details.get("credit_score"),
#                         datetime.now(timezone.utc).isoformat()
#                     ))
#                     conn.commit()
#                 conn.close()
#                 print("✅ Cibil log saved to database.")
#             except Exception as log_err:
#                 print("❌ Error saving log:", log_err)

#             # Final output
#             return {
#                 "status": "success",
#                 "pan_number": pan_number,
#                 "pan_supreme": pan_supreme_data,
#                 "cibil_report": cibil_data,
#                 "profile_detail": user_details
#             }

#     except Exception as e:
#         print("❌ Unhandled Exception:", str(e))
#         traceback.print_exc()
#         return {
#             "status": "error",
#             "message": f"Verification and data fetch failed: {str(e)}"
#         }


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
            print("fetch data", fetch_data)

            return {
                    "consent": "Y",
                    "pan": fetch_data.get("pan_number"),
                    "message": fetch_data.get("message", "OTP verified and data fetched successfully"),
                    "phone_number": phone_number,
                    "cibilScore": fetch_data.get("cibilScore") or fetch_data.get("profile_detail", {}).get("credit_score"),
                    "transId": fetch_data.get("transId") or fetch_data.get("cibil_report", {}).get("transaction_id"),
                    "raw": fetch_data.get("raw") or fetch_data.get("cibil_data") or fetch_data.get("cibil_report"),
                    "approvedLenders": fetch_data.get("approvedLenders") or [],
                    "moreLenders": fetch_data.get("moreLenders") or [],
                    "emi_data": fetch_data.get("emi_data") or {},
                    "data": fetch_data.get("data") or fetch_data.get("cibil_data") or fetch_data.get("cibil_report"),
                    "user_details": fetch_data.get("user_details") or fetch_data.get("profile_detail"),
                    "source": fetch_data.get("source") or "cibil",   # <-- REQUIRED by your model
                    "emi_data": fetch_data.get("emi_data")
                }

        except Exception as e:
            raise HTTPException(status_code=200, detail=f"{str(e)}")


