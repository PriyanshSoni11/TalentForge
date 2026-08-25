import os
from dotenv import load_dotenv

load_dotenv()


def get_config():
    return {
        "SECRET_KEY": os.getenv("SECRET_KEY", "dev"),
        "DEBUG": os.getenv("FLASK_ENV", "development") == "development",
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"),
        "JWT_SECRET": os.getenv("JWT_SECRET", "dev-jwt-secret"),
        "JWT_ACCESS_TOKEN_EXPIRES_MIN": int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MIN", 60)),
        "JWT_REFRESH_TOKEN_EXPIRES_DAYS": int(os.getenv("JWT_REFRESH_TOKEN_EXPIRES_DAYS", 7)),
        "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY"),
        "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
        "RAG_EMBEDDING_MODEL": os.getenv("RAG_EMBEDDING_MODEL", "models/gemini-embedding-001"),
        "SOCKETIO_MESSAGE_QUEUE": os.getenv("SOCKETIO_MESSAGE_QUEUE") or None,
    }
