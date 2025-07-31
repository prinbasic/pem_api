from fastapi import HTTPException
from datetime import datetime
import random
from typing import Optional
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

            # if not mobile_to_prefill_data.get("result") or not mobile_to_prefill_data["result"].get("pan"):
            #     raise HTTPException(
            #         status_code=400,
            #         detail="PAN number not returned in Mobile to Prefill response."
            #     )

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
                            "StreetAddress": pan_details["address"].get("address_line_1", "").strip(),
                            "City": pan_details["address"].get("address_line_5", "").strip(),  # BOKARO
                            "PostalCode": int(pan_details["address"].get("pin_code", 0)),
                            "Region": int(region_code),
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

                print("................................................................................................ userdetail")

                dob = dob_formatted
                print("DOB:", dob)

                credit_score = borrower.get("CreditScore", {}).get("riskScore")
                print("Credit Score:", credit_score)

                email = borrower.get("EmailAddress", [{}]).get("Email", "")
                print("Email:", email)

                # gender = borrower.get("Gender", "")
                # print("Gender:", gender)

                # pan_number = borrower.get("IdentifierPartition", {}).get("Identifier", [{}])[1].get("ID", {}).get("Id", "")
                # print("PAN Number:", pan_number)

                # pincode = borrower.get("BorrowerAddress", [{}])[0].get("CreditAddress", {}).get("PostalCode", "")
                # print("Pincode:", pincode)

                # name = borrower.get("BorrowerName", {}).get("Name", {}).get("Forename", "")
                # print("Name:", name)

                # phone = phone_number
                # print("Phone Number:", phone)

                # user_details = {
                #     "dob": dob_formatted,
                #     "credit_score": borrower.get("CreditScore", {}).get("riskScore"),
                #     "email": borrower.get("EmailAddress", [{}])[0].get("Email"),
                #     "gender": borrower.get("Gender"),
                #     "pan_number": borrower.get("IdentifierPartition", {}).get("Identifier", [{}])[1].get("ID", {}).get("Id"),
                #     "pincode": borrower.get("BorrowerAddress", [{}])[0].get("CreditAddress", {}).get("PostalCode"),
                #     "name": borrower.get("BorrowerName", {}).get("Name", {}).get("Forename"),
                #     "phone": phone_number
                # }

                # print(user_details)

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
                        # user_details.get(""),
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

            return {
                "pan_number": final_pan_number,
                "pan_supreme": pan_supreme_data,
                "cibil_report": cibil_data,
                # "intell_report": intell_response
                # "profile_detail": user_details
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

            return {
                "consent": "Y",
                "message": fetch_data.get("message", "OTP verified and data fetched successfully"),
                "phone_number": phone_number,
                "cibilScore": fetch_data.get("cibilScore"),
                "transId": fetch_data.get("transId"),
                "raw": fetch_data.get("raw"),
                "approvedLenders": fetch_data.get("approvedLenders"),
                "moreLenders": fetch_data.get("moreLenders"),
                "emi_data": fetch_data.get("emi_data"),
                "data": fetch_data.get("data"),
                # "intell_response": fetch_data.get("intell_response"),
                "user_details": fetch_data.get("user_details"),
            }

        except Exception as e:
            raise HTTPException(status_code=200, detail=f"{str(e)}")


