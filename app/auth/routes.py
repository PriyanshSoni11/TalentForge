import uuid

from flask import Blueprint, request, jsonify

from app.extensions import get_supabase
from app.auth.utils import (
    hash_password,
    verify_password,
    generate_access_token,
    generate_refresh_token,
    decode_token,
    token_required,
    ALL_ROLES,
)
from app.ai.file_extract import extract_text_from_resume
from app.ai.resume_parser import parse_resume
import jwt

auth_bp = Blueprint("auth", __name__)

USERS_TABLE = "users"


@auth_bp.post("/register")
def register():
    data = request.form if request.form else (request.get_json(force=True) or {})
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "").strip().lower()
    college_name = (data.get("college_name") or "").strip()

    if not all([email, password, name, role]):
        return jsonify({"error": "email, password, name and role are required"}), 400
    if role not in ALL_ROLES:
        return jsonify({"error": f"role must be one of {sorted(ALL_ROLES)}"}), 400
    if role == "student" and not college_name:
        return jsonify({"error": "college_name is required for student registration"}), 400

    resume_file = request.files.get("resume") if role == "student" else None
    if role == "student" and (not resume_file or not resume_file.filename):
        return jsonify({"error": "resume is required for student registration"}), 400

    resume_bytes = None
    parsed_resume = None
    if resume_file:
        resume_bytes = resume_file.read()
        if not resume_bytes:
            return jsonify({"error": "the uploaded resume is empty"}), 400
        try:
            parsed_resume = parse_resume(extract_text_from_resume(resume_bytes, resume_file.filename))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            return jsonify({"error": "resume processing is unavailable right now. Please try again."}), 503

    supabase = get_supabase()

    existing = supabase.table(USERS_TABLE).select("id").eq("email", email).execute()
    if existing.data:
        return jsonify({"error": "an account with this email already exists"}), 409

    record = {
        "email": email,
        "password_hash": hash_password(password),
        "name": name,
        "role": role,
    }
    result = supabase.table(USERS_TABLE).insert(record).execute()
    user = result.data[0]

    if role == "student":
        storage_path = f"{user['id']}/{uuid.uuid4()}_{resume_file.filename}"
        try:
            supabase.storage.from_("resumes").upload(storage_path, resume_bytes)
        except Exception:
            try:
                supabase.table(USERS_TABLE).delete().eq("id", user["id"]).execute()
            except Exception:
                pass
            return jsonify({
                "error": "resume storage is unavailable. Configure SUPABASE_SERVICE_ROLE_KEY and ensure the resumes bucket exists."
            }), 503
        resume_url = supabase.storage.from_("resumes").get_public_url(storage_path)
        supabase.table("student_profiles").upsert({
            "user_id": user["id"], "college_name": college_name,
            "resume_url": resume_url,
            "resume_path": storage_path,
            "parsed_resume": parsed_resume,
        }, on_conflict="user_id").execute()
    elif role == "industry":
        headline = f"Mentor · {college_name}" if college_name else "Mentor & Industry Professional"
        bio = f"College: {college_name}" if college_name else "Available for student mentorship"
        supabase.table("mentor_profiles").upsert({
            "user_id": user["id"],
            "headline": headline,
            "bio": bio,
            "expertise": ["Career Mentorship", "Technical Guidance"],
            "availability": "Open to requests",
            "accepting_requests": True
        }, on_conflict="user_id").execute()

    access = generate_access_token(user["id"], user["role"])
    refresh = generate_refresh_token(user["id"])
    return jsonify({
        "user": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]},
        "access_token": access,
        "refresh_token": refresh,
    }), 201


@auth_bp.post("/login")
def login():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password")
    requested_role = (data.get("role") or "").strip().lower()

    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    if requested_role not in ALL_ROLES:
        return jsonify({"error": "choose a student or industry login"}), 400

    supabase = get_supabase()
    result = supabase.table(USERS_TABLE).select("*").eq("email", email).execute()
    if not result.data:
        return jsonify({"error": "invalid credentials"}), 401

    user = result.data[0]
    if user["role"] != requested_role:
        return jsonify({"error": "this account belongs to the other portal"}), 403
    if not verify_password(password, user["password_hash"]):
        return jsonify({"error": "invalid credentials"}), 401

    access = generate_access_token(user["id"], user["role"])
    refresh = generate_refresh_token(user["id"])
    return jsonify({
        "user": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]},
        "access_token": access,
        "refresh_token": refresh,
    })


@auth_bp.post("/refresh")
def refresh():
    data = request.get_json(force=True) or {}
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400

    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise jwt.InvalidTokenError("wrong token type")
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "refresh token expired, please log in again"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "invalid refresh token"}), 401

    supabase = get_supabase()
    result = supabase.table(USERS_TABLE).select("id, role").eq("id", payload["sub"]).execute()
    if not result.data:
        return jsonify({"error": "user no longer exists"}), 401

    user = result.data[0]
    return jsonify({"access_token": generate_access_token(user["id"], user["role"])})


@auth_bp.get("/me")
@token_required
def me():
    supabase = get_supabase()
    result = supabase.table(USERS_TABLE).select("id, email, name, role").eq("id", request.user["id"]).execute()
    if not result.data:
        return jsonify({"error": "user not found"}), 404
    return jsonify(result.data[0])


@auth_bp.get("/activity")
@token_required
def recent_activity():
    result = get_supabase().table("activity_log").select(
        "id, type, title, subtitle, created_at"
    ).eq("user_id", request.user["id"]).order("created_at", desc=True).limit(10).execute()
    return jsonify(result.data)


@auth_bp.get("/profile")
@token_required
def profile():
    supabase = get_supabase()
    user_result = supabase.table(USERS_TABLE).select("id, email, name, role").eq(
        "id", request.user["id"]
    ).execute()
    if not user_result.data:
        return jsonify({"error": "user not found"}), 404

    user = user_result.data[0]
    details = {}
    if user["role"] == "student":
        result = supabase.table("student_profiles").select("college_name").eq(
            "user_id", user["id"]
        ).execute()
        details = result.data[0] if result.data else {}
    else:
        result = supabase.table("mentor_profiles").select(
            "headline, bio, expertise, availability, accepting_requests"
        ).eq("user_id", user["id"]).execute()
        details = result.data[0] if result.data else {}
    return jsonify({"user": user, "profile": details})


@auth_bp.put("/profile")
@token_required
def update_profile():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()[:120]
    if not name:
        return jsonify({"error": "name is required"}), 400

    supabase = get_supabase()
    supabase.table(USERS_TABLE).update({"name": name}).eq(
        "id", request.user["id"]
    ).execute()

    if request.user["role"] == "student":
        college_name = (data.get("college_name") or "").strip()[:160]
        supabase.table("student_profiles").upsert({
            "user_id": request.user["id"], "college_name": college_name or None,
        }, on_conflict="user_id").execute()
    else:
        expertise = data.get("expertise") or []
        if not isinstance(expertise, list) or not all(isinstance(skill, str) for skill in expertise):
            return jsonify({"error": "expertise must be a list of strings"}), 400
        supabase.table("mentor_profiles").upsert({
            "user_id": request.user["id"],
            "headline": (data.get("headline") or "Industry professional").strip()[:120],
            "bio": (data.get("bio") or "").strip()[:1000] or None,
            "expertise": [skill.strip()[:50] for skill in expertise[:12] if skill.strip()],
            "availability": (data.get("availability") or "Open to requests").strip()[:80],
            "accepting_requests": bool(data.get("accepting_requests", True)),
        }, on_conflict="user_id").execute()
    return jsonify({"message": "Profile updated"})


@auth_bp.delete("/profile/mentor")
@token_required
def delete_mentor_profile():
    if request.user["role"] != "industry":
        return jsonify({"error": "Only industry users have mentor profiles"}), 403
    
    supabase = get_supabase()
    result = supabase.table("mentor_profiles").delete().eq("user_id", request.user["id"]).execute()
    if not result.data:
        return jsonify({"error": "Mentor profile not found"}), 404
    return jsonify({"success": True})
