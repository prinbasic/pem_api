from db_client import get_db_connection
from models.request_models import LoanFormData
from datetime import datetime
import json
import traceback

def log_user_cibil_data(form_data: LoanFormData, response_data: dict, emi_data: list = None):
    print("📝 Logging for PAN:", form_data.pan)
    print("🔁 INSERTING with EMI data:", json.dumps(emi_data or []))
    print("🧪 FINAL EMI DATA:", emi_data, type(emi_data))


    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_cibil_logs (
                    name, email, pan, phone, dob, location,
                    cibil_score, lender_matches, raw_report, created_at, emi_details
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (pan) DO UPDATE SET
                    name = EXCLUDED.name,
                    email = EXCLUDED.email,
                    phone = EXCLUDED.phone,
                    dob = EXCLUDED.dob,
                    location = EXCLUDED.location,
                    cibil_score = EXCLUDED.cibil_score,
                    lender_matches = EXCLUDED.lender_matches,
                    raw_report = EXCLUDED.raw_report,
                    created_at = EXCLUDED.created_at,
                    emi_details = EXCLUDED.emi_details
            """, (
                form_data.name,
                form_data.email,
                form_data.pan,
                form_data.phone,
                form_data.dob[:10],
                form_data.location,
                response_data.get("cibilScore"),
                json.dumps(response_data.get("topMatches", []) + response_data.get("moreLenders", [])),
                json.dumps(response_data.get("raw") or response_data.get("report")),
                datetime.now(),
                json.dumps(emi_data or [])
            ))

        conn.commit()
        conn.close()
        print("✅ cibil + EMI inserted/updated")
    except Exception as e:
        print("❌ Logging error:")
        traceback.print_exc()
