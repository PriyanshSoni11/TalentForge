from flask_socketio import SocketIO
from supabase import create_client, Client

socketio = SocketIO(cors_allowed_origins="*", async_mode="eventlet")

_supabase_client = None


def init_supabase(app):
    global _supabase_client
    if _supabase_client is None:
        url = app.config["SUPABASE_URL"]
        key = app.config["SUPABASE_KEY"]
        if not url or not key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set. Copy .env.example to .env and fill them in.")
        _supabase_client = create_client(url, key)
    return _supabase_client


def get_supabase():
    if _supabase_client is None:
        raise RuntimeError("Supabase client not initialized yet. Call init_supabase(app) first.")
    return _supabase_client
