from fastapi import APIRouter
from models.request_models import LoanFormData
from db_client import get_db_connection

router = APIRouter()

# @router.post("/bank_by_cibil")
# def get_lenders(data: LoanFormData):
#     try:
#         score = data.cibilScore
#         conn = get_db_connection()
#         with conn.cursor() as cur:
#             cur.execute("""
#                 SELECT * FROM lenders
#                 WHERE CAST(LEFT(minimum_cibil_score, 3) AS INTEGER) < %s
#             """, (score,))
#             rows = cur.fetchall()
#             col_names = [desc[0] for desc in cur.description]
#         conn.close()

#         lenders = [dict(zip(col_names, row)) for row in rows]
#         return {
#             "message": "Lenders matched successfully.",
#             "topMatches": lenders[:3],
#             "moreLenders": lenders[3:6]
#         }

#     except Exception as e:
#         return {"error": str(e)}

def get_matching_lenders(cibil_score: int):
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
    SELECT lender_name, lender_type, home_loan_roi, lap_roi,
           home_loan_ltv, remarks, loan_approval_time, processing_time,
           minimum_loan_amount, maximum_loan_amount
    FROM lenders
    WHERE CAST(LEFT(minimum_cibil_score, 3) AS INTEGER) <= %s
      AND home_loan_roi IS NOT NULL
      AND home_loan_roi != ''
    ORDER BY 
        CAST(REPLACE(SPLIT_PART(home_loan_roi, '-', 1), '%%', '') AS FLOAT)
""", (cibil_score,))


            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
        conn.close()
        return [dict(zip(col_names, row)) for row in rows]
    except Exception as e:
        return {"error": str(e)}