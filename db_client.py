import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    try:
        conn = psycopg.connect(
            host=os.getenv("SUPABASE_DB_HOST"),
            port=os.getenv("SUPABASE_DB_PORT"),
            dbname=os.getenv("SUPABASE_DB_NAME"),
            user=os.getenv("SUPABASE_DB_USER"),
            password=os.getenv("SUPABASE_DB_PASSWORD"),
            autocommit=True  # Optional: good for basic inserts/selects
        )
        return conn
    except Exception as e:
        print("❌ Failed to connect to DB:", str(e))
        raise e
