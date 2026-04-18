import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def get_engine():
    # Try Streamlit secrets first (cloud), then .env (local)
    try:
        import streamlit as st
        db_url = st.secrets["DATABASE_URL"]
    except Exception:
        db_url = os.getenv("DATABASE_URL")
    
    if not db_url:
        raise ValueError("DATABASE_URL not found in secrets or environment")
    
    return create_engine(db_url)

def test_connection():
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        return str(e)