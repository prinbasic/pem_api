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
import re
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

def _parse_dob(d):
    # accepts "31-12-1999" → "1999-12-31"; passes through if already ISO-ish
    try:
        return datetime.strptime(d, "%d-%m-%Y").strftime("%Y-%m-%d")
    except Exception:
        return d or ""

def _pick_prefill_address(addr_list):
    # choose first address with a postal_code; map to {state, pin_code, address_line_*}
    for a in addr_list or []:
        if a.get("postal_code"):
            return {
                "state": (a.get("state") or "").strip(),
                "pin_code": str(a.get("postal_code")).strip(),
                "address_line_1": " ".join(filter(None, [
                    a.get("first_line_of_address"), a.get("second_line_of_address"), a.get("third_line_of_address")
                ])).strip(),
                "address_line_5": (a.get("city") or "").strip(),
            }
    return {"state": "", "pin_code": "0", "address_line_1": "", "address_line_5": ""}

def _normalize_from_prefill(prefill_result: dict) -> dict:
    # prefill_result is your `result` object from the prefill API
    full_name = (prefill_result.get("name") or "").strip()
    parts = [p for p in full_name.split() if p]
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
    gender_raw = (prefill_result.get("gender") or "").strip().lower()
    gender_norm = "M" if gender_raw.startswith("m") else ("F" if gender_raw.startswith("f") else "")

    return {
        "first_name": first_name,
        "last_name": last_name,
        "dob": _parse_dob(prefill_result.get("dob") or ""),
        "gender": gender_norm,  # now your existing "M" check works
        "pan": prefill_result.get("pan", "").strip(),
        "email": (prefill_result.get("email") or "").strip(),
        "address": _pick_prefill_address(prefill_result.get("address")),
    }

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

            # # PAN Supreme
            # pan_supreme_resp = await client.post(
            #     PAN_SUPREME_URL,
            #     headers=HEADERS,
            #     json={"pan": final_pan_number}
            # )
            # pan_supreme_data = pan_supreme_resp.json()
            # print(f"🔍 PAN Supreme API Response: {pan_supreme_data}")

            # if pan_supreme_data.get("status") != "1":
            #     print(final_pan_number)
            #     raise HTTPException(
            #         status_code=400,
            #         detail=f"PAN Supreme verification failed: {pan_supreme_data.get('message', 'No message')}"
            #     )
            
            # Try PAN Supreme first
            try:
                pan_supreme_resp = await client.post(
                    PAN_SUPREME_URL,
                    headers=HEADERS,
                    json={"pan": final_pan_number}
                )
                pan_supreme_data = pan_supreme_resp.json()
                print(f"🔍 PAN Supreme API Response: {pan_supreme_data}")
                ok_supreme = (pan_supreme_resp.status_code == 200 and pan_supreme_data.get("status") == "1")
            except Exception as e:
                print("PAN Supreme request error:", e)
                ok_supreme = False

            if ok_supreme:
                pan_details = pan_supreme_data["result"]  # expected to already match your schema
                print(f"✅ PAN Supreme Details: {pan_details}")
            else:
                print("⚠️ PAN Supreme failed—using PAN-to-prefill fallback")
                # ---- paste/assign your prefill JSON dict to `prefill_payload` (or fetch it) ----
                # prefill_payload = <the dict you showed in the message>
                # Example:
                # prefill_payload = {...}
                if not mobile_to_prefill_data or mobile_to_prefill_data.get("http_response_code") != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"PAN Supreme failed and prefill unavailable"
                    )
                if str(mobile_to_prefill_data.get("result_code")) not in {"101"}:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Prefill response not successful: {mobile_to_prefill_data.get('message')}"
                    )
                pan_details = _normalize_from_prefill(mobile_to_prefill_data["result"])
                print(f"✅ Using normalized prefill details: {pan_details}")


            pan_details = pan_supreme_data["result"]
            print(f"✅ PAN Supreme Details: {pan_details}")

            state_name = pan_details.get("address", {}).get("state", "").strip()
            print(state_name)

            region_code_int = STATE_CODE_MAPPING.get(state_name, 97)  # default to 97 for "Other Territory"
            region_code = f"{region_code_int:02}"  # zero-pad if less than 10

            print(region_code)

            EMAIL_SENTINELS = {"", "na", "n/a", "null", "none", "0", "-"}

            def normalize_email(e: str) -> str:
                s = (e or "").strip().lower()
                if s in EMAIL_SENTINELS:
                    return ""
                # simple, robust RFC-like check
                return s if re.match(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", s) else ""

            def pick_email(*candidates) -> str:
                for c in candidates:
                    e = normalize_email(c)
                    if e:
                        return e
                return ""  # no valid email found
            
            region_dummy = 20

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
                        "EmailID": "prince.raj@basichomeloan.com",
                        "DateOfBirth": pan_details.get("dob", "").strip(),  # Format: YYYY-MM-DD
                        "PhoneNumber": {
                            "Number": int(phone_number)
                        },
                        "Gender": "Male" if pan_details["gender"].upper() == "M" else "Female"
                    },
                    "LegalCopyStatus": "Accept",
                    "UserConsentForDataSharing": True
                }
                print(f"cibil payload", cibil_payload)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"CIBIL payload creation failed: {str(e)}")

            cibil_resp = await client.post(CIBIL_URL, headers=HEADERS, json=cibil_payload)
            print()
            cibil_data = cibil_resp.json()

            print(f"cibil data : {cibil_data}")

            # try:
            #     if cibil_data.get("result").get("status") == "error":
            #         raise HTTPException(status_code=500, detail=f"CIBIL extraction failed")
            # except:

            try:
                if cibil_data.get("result", {}).get("status") == "error":
                    raise Exception("CIBIL extraction failed")
                print(f"cibil data : {cibil_data}")
                # ---- Compute sum of active EMIs (no dateClosed and currentBalance != "0") ----
                tlp = (
                    # ...your code for EMI sum computation...
                )
            except Exception as e:
                # Convert to HTTPException
                raise HTTPException(status_code=500, detail=f"{e}")
                


            print(f"cibil data : {cibil_data}")

            # ---- Compute sum of active EMIs (no dateClosed and currentBalance != "0") ----
            tlp = (
                cibil_data.get("data", {})
                        .get("cibilData", {})
                        .get("GetCustomerAssetsResponse", {})
                        .get("GetCustomerAssetsSuccess", {})
                        .get("Asset", {})
                        .get("TrueLinkCreditReport", {})
                        .get("TradeLinePartition")
            )

            # Normalize to a list
            if tlp is None:
                tradelines = []
            elif isinstance(tlp, list):
                tradelines = tlp
            elif isinstance(tlp, dict):
                tradelines = [tlp]
            else:
                tradelines = []

            active_total = 0.0
            seen = set()  # optional de-dup by (subscriberCode, accountNumber)

            for item in tradelines:
                t = item.get("Tradeline", {}) if isinstance(item, dict) else {}
                g = t.get("GrantedTrade", {}) if isinstance(t, dict) else {}

                # active if not closed and currentBalance != "0"
                if t.get("dateClosed"):
                    continue
                if str(t.get("currentBalance", "")).strip() == "0":
                    continue

                emi_raw = g.get("EMIAmount")
                if emi_raw in (None, "", "-1", "-1.00"):
                    continue

                # to float
                try:
                    emi_val = float(str(emi_raw).replace(",", "").strip())
                except Exception:
                    emi_val = None

                if emi_val and emi_val > 0:
                    key = (t.get("subscriberCode"), t.get("accountNumber"))
                    if key in seen:
                        continue
                    seen.add(key)
                    active_total += emi_val

            active_emi_sum = max(active_total, 0.0)  # clamp negatives to 0

            try:
                borrower = (
                    cibil_data.get("data", {})
                    .get("cibilData", {})
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

                # pan_number = borrower.get("IdentifierPartition", {}).get("Identifier", [{}])[1].get("ID", {}).get("Id", "")
                pan_number = final_pan_number.strip()
                print("PAN Number:", pan_number)

                pincode = borrower.get("BorrowerAddress", [{}])[0].get("CreditAddress", {}).get("PostalCode", "")
                print("Pincode:", pincode)

                # name = borrower.get("BorrowerName", {}).get("Name", {}).get("Forename") +" " + borrower.get("BorrowerName", {}).get("Name", {}).get("Surname")
                name = mobile_to_prefill_data.get("result").get("name")
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

            consent = "Y"

            try:
                conn = get_db_connection()
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_cibil_logs (
                            pan, dob, name, phone, location, email,
                            raw_report, cibil_score, created_at, monthly_emi, consent
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                            consent = EXCLUDED.consent
                    """, (
                        pan_details.get("pan"),
                        pan_details.get("dob"),
                        f"{pan_details.get('first_name', '')} {pan_details.get('last_name', '')}".strip(),
                        phone_number,
                        pan_details.get("address", {}).get("pin_code"),
                        pan_details.get("email", None),
                        json.dumps(cibil_data),
                        user_details.get("credit_score"),
                        datetime.now(timezone.utc).isoformat(),
                        active_emi_sum,
                        consent
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
                # "intell_report": intell_response
                "profile_detail": user_details,
                "source": "cibil",
                "emi_data": active_emi_sum
            }
    except Exception as e:
        exc_type, exc_obj, tb = sys.exc_info()
        fname = tb.tb_frame.f_code.co_filename
        lineno = tb.tb_lineno

        print("⚠️ TransBank failed")
        print(f"   Error Type : {exc_type.__name__}")
        print(f"   Message    : {e}")
        print(f"   File       : {fname}")
        print(f"   Line       : {lineno}")

        # Optional: dump recent API responses if you want to see why schema parsing failed
        if 'mobile_to_prefill_data' in locals():
            print("   Last Mobile-to-Prefill data:", mobile_to_prefill_data)
        if 'pan_supreme_data' in locals():
            print("   Last PAN Supreme data:", pan_supreme_data)

        print("👉 Trying fallback via Ongrid...")
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

            try:
                emi_data =  fetch_data.get("emi_data")
            except:
                emi_data =  None

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
                    "emi_data": emi_data
                }

        except Exception as e:
            raise HTTPException(status_code=200, detail=f"{str(e)}")






