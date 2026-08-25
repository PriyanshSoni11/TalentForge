from flask import request
from flask_socketio import join_room, emit

from app.extensions import socketio
from app.auth.utils import decode_token

_connected_users = {}


@socketio.on("connect")
def handle_connect(auth):
    token = (auth or {}).get("token")
    if not token:
        return False

    try:
        payload = decode_token(token)
    except Exception:
        return False

    user_id = payload["sub"]
    role = payload["role"]

    join_room(f"user_{user_id}")
    join_room(f"{role}s_room")
    _connected_users[request.sid] = user_id

    emit("connected", {"message": "connected", "user_id": user_id})


@socketio.on("disconnect")
def handle_disconnect():
    _connected_users.pop(request.sid, None)


@socketio.on("chat_message")
def handle_chat_message(data):
    to_user_id = data.get("to_user_id")
    message = data.get("message")
    if not to_user_id or not message:
        return

    sender_id = _connected_users.get(request.sid)
    emit("chat_message", {"from_user_id": sender_id, "message": message}, to=f"user_{to_user_id}")
