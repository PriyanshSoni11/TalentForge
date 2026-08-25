import datetime

from flask import Blueprint, request, jsonify

from app.extensions import get_supabase, socketio
from app.auth.utils import token_required, roles_required, ROLE_INDUSTRY
from app.ai.chat import answer_industry_question
from app.industry.insights import pick_next_best_action, compute_conversion_rate

industry_bp = Blueprint("industry", __name__)


@industry_bp.put("/mentor-profile")
@token_required
@roles_required(ROLE_INDUSTRY)
def update_mentor_profile():
    data = request.get_json(force=True) or {}
    headline = (data.get("headline") or "Industry professional").strip()[:120]
    bio = (data.get("bio") or "").strip()[:1000] or None
    expertise = data.get("expertise") or []
    if not isinstance(expertise, list) or not all(isinstance(skill, str) for skill in expertise):
        return jsonify({"error": "expertise must be a list of strings"}), 400

    profile = {
        "user_id": request.user["id"],
        "headline": headline,
        "bio": bio,
        "expertise": [skill.strip()[:50] for skill in expertise[:12] if skill.strip()],
        "availability": (data.get("availability") or "Open to requests").strip()[:80],
        "accepting_requests": bool(data.get("accepting_requests", True)),
    }
    result = get_supabase().table("mentor_profiles").upsert(profile, on_conflict="user_id").execute()
    return jsonify(result.data[0])


@industry_bp.get("/mentorship/requests")
@token_required
@roles_required(ROLE_INDUSTRY)
def incoming_mentorship_requests():
    result = get_supabase().table("mentorship_requests").select(
        "id, student_id, message, status, created_at, users!mentorship_requests_student_id_fkey(name, email)"
    ).eq("mentor_id", request.user["id"]).order("created_at", desc=True).execute()
    return jsonify(result.data)


@industry_bp.patch("/mentorship/requests/<request_id>")
@token_required
@roles_required(ROLE_INDUSTRY)
def update_mentorship_request(request_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in {"accepted", "declined"}:
        return jsonify({"error": "status must be accepted or declined"}), 400
    result = get_supabase().table("mentorship_requests").update({"status": status}).eq(
        "id", request_id
    ).eq("mentor_id", request.user["id"]).eq("status", "pending").execute()
    if not result.data:
        return jsonify({"error": "pending request not found"}), 404
    return jsonify(result.data[0])


@industry_bp.get("/network")
@token_required
@roles_required(ROLE_INDUSTRY)
def industry_network():
    supabase = get_supabase()
    users = supabase.table("users").select("id, name, email").eq("role", "industry").neq(
        "id", request.user["id"]
    ).execute().data
    profiles = supabase.table("mentor_profiles").select(
        "user_id, headline, expertise, availability"
    ).execute().data
    profiles_by_user = {profile["user_id"]: profile for profile in profiles}
    return jsonify([{**user, **profiles_by_user.get(user["id"], {})} for user in users])


@industry_bp.get("/events")
@token_required
@roles_required(ROLE_INDUSTRY)
def industry_events():
    return jsonify(get_supabase().table("events").select("*").eq(
        "created_by", request.user["id"]
    ).order("starts_at").execute().data)


@industry_bp.post("/events")
@token_required
@roles_required(ROLE_INDUSTRY)
def create_event():
    data = request.get_json(force=True) or {}
    required = ["title", "description", "location", "starts_at"]
    if not all((data.get(field) or "").strip() for field in required):
        return jsonify({"error": f"{required} are required"}), 400
    try:
        starts_at = datetime.datetime.fromisoformat(data["starts_at"].replace("Z", "+00:00"))
    except ValueError:
        return jsonify({"error": "starts_at must be a valid ISO date"}), 400
    event = {
        "created_by": request.user["id"], "title": data["title"].strip()[:160],
        "description": data["description"].strip()[:2000], "location": data["location"].strip()[:160],
        "starts_at": starts_at.isoformat(), "ends_at": data.get("ends_at"),
        "capacity": data.get("capacity"),
    }
    result = get_supabase().table("events").insert(event).execute()
    return jsonify(result.data[0]), 201


@industry_bp.post("/jobs")
@token_required
@roles_required(ROLE_INDUSTRY)
def create_job():
    data = request.get_json(force=True) or {}
    required = ["title", "description", "required_skills", "type"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": f"{required} are required"}), 400
    if data["type"] not in {"job", "internship", "apprenticeship"}:
        return jsonify({"error": "type must be 'job', 'internship', or 'apprenticeship'"}), 400

    skills = data["required_skills"]
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    elif isinstance(skills, list):
        skills = [str(s).strip() for s in skills if str(s).strip()]
    else:
        skills = []

    supabase = get_supabase()
    record = {
        "posted_by": request.user["id"],
        "title": str(data["title"]).strip(),
        "description": str(data["description"]).strip(),
        "required_skills": skills,
        "type": data["type"],
        "location": (data.get("location") or "Remote").strip(),
        "status": data.get("status") if data.get("status") in {"open", "closed"} else "open",
    }
    try:
        job = supabase.table("job_postings").insert(record).execute().data[0]
        socketio.emit("new_job_posting", job, to="students_room")
        return jsonify(job), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@industry_bp.get("/jobs/mine")
@token_required
@roles_required(ROLE_INDUSTRY)
def my_jobs():
    supabase = get_supabase()
    result = supabase.table("job_postings").select("*").eq("posted_by", request.user["id"]).order("created_at", desc=True).execute()
    return jsonify(result.data or [])


@industry_bp.put("/jobs/<job_id>")
@token_required
@roles_required(ROLE_INDUSTRY)
def update_job(job_id):
    data = request.get_json(force=True) or {}
    required = ["title", "description", "required_skills", "type"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": f"{required} are required"}), 400
    if data["type"] not in {"job", "internship", "apprenticeship"}:
        return jsonify({"error": "type must be 'job', 'internship', or 'apprenticeship'"}), 400

    skills = data["required_skills"]
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    elif isinstance(skills, list):
        skills = [str(s).strip() for s in skills if str(s).strip()]
    else:
        skills = []

    supabase = get_supabase()
    record = {
        "title": str(data["title"]).strip(),
        "description": str(data["description"]).strip(),
        "required_skills": skills,
        "type": data["type"],
        "location": (data.get("location") or "Remote").strip(),
    }
    if "status" in data and data["status"] in {"open", "closed"}:
        record["status"] = data["status"]

    try:
        result = supabase.table("job_postings").update(record).eq("id", job_id).eq("posted_by", request.user["id"]).execute()
        if not result.data:
            return jsonify({"error": "job not found or unauthorized"}), 404
        return jsonify(result.data[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@industry_bp.delete("/jobs/<job_id>")
@token_required
@roles_required(ROLE_INDUSTRY)
def delete_job(job_id):
    supabase = get_supabase()
    try:
        # Verify ownership
        owned = supabase.table("job_postings").select("id").eq("id", job_id).eq("posted_by", request.user["id"]).execute()
        if not owned.data:
            return jsonify({"error": "Job posting not found or unauthorized"}), 404

        # Clean up related saved_jobs
        try:
            supabase.table("saved_jobs").delete().eq("job_posting_id", job_id).execute()
        except Exception:
            pass

        # Clean up related applications
        try:
            supabase.table("applications").delete().eq("job_posting_id", job_id).execute()
        except Exception:
            pass

        # Clean up related interviews & questions
        try:
            interviews = supabase.table("interviews").select("id").eq("job_posting_id", job_id).execute().data
            if interviews:
                for iv in interviews:
                    try:
                        supabase.table("interview_questions").delete().eq("interview_id", iv["id"]).execute()
                    except Exception:
                        pass
                supabase.table("interviews").delete().eq("job_posting_id", job_id).execute()
        except Exception:
            pass

        # Delete the job posting
        result = supabase.table("job_postings").delete().eq("id", job_id).eq("posted_by", request.user["id"]).execute()
        return jsonify({"success": True, "message": "Job posting deleted successfully"})
    except Exception as e:
        return jsonify({"error": f"Failed to delete job posting: {str(e)}"}), 500


@industry_bp.post("/courses")
@token_required
@roles_required(ROLE_INDUSTRY)
def publish_course():
    data = request.get_json(force=True) or {}
    required = ["title", "description", "skills_covered"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": f"{required} are required"}), 400

    skills = data["skills_covered"]
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    elif isinstance(skills, list):
        skills = [str(s).strip() for s in skills if str(s).strip()]
    else:
        skills = []

    supabase = get_supabase()
    record = {
        "published_by": request.user["id"],
        "title": str(data["title"]).strip(),
        "description": str(data["description"]).strip(),
        "skills_covered": skills,
        "content_url": data.get("content_url"),
    }
    try:
        course = supabase.table("courses").insert(record).execute().data[0]
        socketio.emit("new_course", course, to="students_room")
        return jsonify(course), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@industry_bp.get("/courses/mine")
@token_required
@roles_required(ROLE_INDUSTRY)
def my_courses():
    supabase = get_supabase()
    result = supabase.table("courses").select("*").eq("published_by", request.user["id"]).order("created_at", desc=True).execute()
    return jsonify(result.data or [])


@industry_bp.put("/courses/<course_id>")
@token_required
@roles_required(ROLE_INDUSTRY)
def update_course(course_id):
    data = request.get_json(force=True) or {}
    required = ["title", "description", "skills_covered"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": f"{required} are required"}), 400

    skills = data["skills_covered"]
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    elif isinstance(skills, list):
        skills = [str(s).strip() for s in skills if str(s).strip()]
    else:
        skills = []

    supabase = get_supabase()
    record = {
        "title": str(data["title"]).strip(),
        "description": str(data["description"]).strip(),
        "skills_covered": skills,
        "content_url": data.get("content_url"),
    }
    try:
        result = supabase.table("courses").update(record).eq("id", course_id).eq("published_by", request.user["id"]).execute()
        if not result.data:
            return jsonify({"error": "course not found or unauthorized"}), 404
        return jsonify(result.data[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@industry_bp.delete("/courses/<course_id>")
@token_required
@roles_required(ROLE_INDUSTRY)
def delete_course(course_id):
    supabase = get_supabase()
    try:
        # Delete related enrollments first
        try:
            supabase.table("course_enrollments").delete().eq("course_id", course_id).execute()
        except Exception:
            pass
        result = supabase.table("courses").delete().eq("id", course_id).eq("published_by", request.user["id"]).execute()
        if not result.data:
            return jsonify({"error": "course not found or unauthorized"}), 404
        return jsonify({"success": True, "message": "Course deleted successfully"})
    except Exception as e:
        return jsonify({"error": f"Failed to delete course: {str(e)}"}), 500


@industry_bp.get("/jobs/<job_id>/applicants")
@token_required
@roles_required(ROLE_INDUSTRY)
def applicants(job_id):
    supabase = get_supabase()
    owned = supabase.table("job_postings").select("id, title").eq("id", job_id).eq("posted_by", request.user["id"]).execute()
    if not owned.data:
        return jsonify({"error": "not found"}), 404

    try:
        result = supabase.table("applications").select(
            "*, users(id, name, email), student_profiles(parsed_resume, strengths, weaknesses, validated_skills, college_name), "
            "interviews(overall_score_pct, passed, status)"
        ).eq("job_posting_id", job_id).order("created_at", desc=True).execute()
        apps_data = result.data or []
    except Exception:
        result = supabase.table("applications").select(
            "*, student_profiles(parsed_resume, strengths, weaknesses, validated_skills, college_name), "
            "interviews(overall_score_pct, passed, status)"
        ).eq("job_posting_id", job_id).order("created_at", desc=True).execute()
        apps_data = result.data or []
        # Enrich users manually if joined syntax had issues
        student_ids = [row["student_id"] for row in apps_data if row.get("student_id")]
        if student_ids:
            try:
                users_res = supabase.table("users").select("id, name, email").in_("id", student_ids).execute().data
                user_map = {u["id"]: u for u in (users_res or [])}
                for row in apps_data:
                    row["users"] = user_map.get(row.get("student_id"), {})
            except Exception:
                pass

    recruiter_rows = supabase.table("users").select("name").eq("id", request.user["id"]).execute().data
    recruiter_name = recruiter_rows[0]["name"] if recruiter_rows else "A recruiter"

    for row in apps_data:
        student_id = row.get("student_id")
        if student_id:
            try:
                supabase.table("activity_log").insert({
                    "user_id": student_id, "type": "portfolio_viewed",
                    "title": "Portfolio viewed", "subtitle": f"{recruiter_name} viewed your profile",
                }).execute()
                current = supabase.table("student_profiles").select("portfolio_views").eq("user_id", student_id).execute().data
                current_views = current[0]["portfolio_views"] if current else 0
                supabase.table("student_profiles").update({"portfolio_views": (current_views or 0) + 1}).eq("user_id", student_id).execute()
            except Exception:
                pass

    return jsonify(apps_data)


@industry_bp.get("/college-students")
@token_required
@roles_required(ROLE_INDUSTRY)
def college_students():
    supabase = get_supabase()
    uid = request.user["id"]

    # 1. Look up mentor's college from mentor_profiles
    mentor_prof = supabase.table("mentor_profiles").select("headline, bio").eq("user_id", uid).execute().data
    mentor_college = ""
    if mentor_prof:
        bio = mentor_prof[0].get("bio") or ""
        headline = mentor_prof[0].get("headline") or ""
        if "College:" in bio:
            mentor_college = bio.split("College:")[1].strip().split("\n")[0].strip()
        elif "·" in headline:
            mentor_college = headline.split("·")[1].strip()
        elif "at " in bio:
            mentor_college = bio.split("at ")[1].strip()
        elif "at " in headline:
            mentor_college = headline.split("at ")[1].strip()
        else:
            mentor_college = headline.replace("Mentor", "").strip(" ·-")

    # 2. Query students
    all_students = supabase.table("student_profiles").select(
        "user_id, college_name, validated_skills, strengths, weaknesses, parsed_resume, portfolio_views, technical_pct, users!inner(name, email)"
    ).execute().data

    # Filter by college if mentor has a college specified
    matched_students = []
    if mentor_college:
        c_lower = mentor_college.lower().strip()
        # Clean acronyms like JEC or IIT Bombay
        matched_students = [
            s for s in all_students
            if s.get("college_name") and (
                c_lower in s["college_name"].lower() or 
                s["college_name"].lower() in c_lower or 
                any(part.lower() in s["college_name"].lower() for part in c_lower.split() if len(part) > 2)
            )
        ]
    
    if not matched_students:
        matched_students = all_students

    # 3. Retrieve assessment scores
    student_ids = [s["user_id"] for s in matched_students]
    scores_by_student = {}
    if student_ids:
        assessments = supabase.table("assessments").select("student_id, score_pct, status").in_("student_id", student_ids).execute().data
        for a in assessments:
            sid = a["student_id"]
            if a.get("score_pct") is not None:
                if sid not in scores_by_student or a["score_pct"] > scores_by_student[sid]:
                    scores_by_student[sid] = a["score_pct"]

    results = []
    for s in matched_students:
        sid = s["user_id"]
        parsed = s.get("parsed_resume") or {}
        degree = "B.Tech Student"
        if parsed.get("education") and len(parsed["education"]) > 0:
            degree = parsed["education"][0].get("degree") or "B.Tech Student"

        skills = s.get("validated_skills") or parsed.get("skills") or ["Technical Fundamentals"]
        score = scores_by_student.get(sid, round(s.get("technical_pct") or 82.0))

        results.append({
            "id": sid,
            "name": s["users"]["name"],
            "email": s["users"]["email"],
            "college_name": s.get("college_name") or mentor_college or "College Student",
            "degree": degree,
            "skills": skills[:6],
            "strengths": s.get("strengths") or [],
            "score_pct": score,
            "portfolio_views": s.get("portfolio_views", 0),
            "projects_count": len(parsed.get("projects") or []),
            "experience_count": len(parsed.get("experience") or []),
        })

    # Sort students by highest readiness score
    results = sorted(results, key=lambda x: x["score_pct"], reverse=True)

    return jsonify({
        "mentor_college": mentor_college,
        "total_students": len(results),
        "students": results
    })


@industry_bp.get("/candidates/recommended")
@token_required
@roles_required(ROLE_INDUSTRY)
def recommended_candidates():
    supabase = get_supabase()
    uid = request.user["id"]

    jobs = supabase.table("job_postings").select("id, title").eq("posted_by", uid).execute().data
    job_ids = [j["id"] for j in jobs]
    if not job_ids:
        return jsonify([])

    applications = supabase.table("applications").select(
        "id, job_posting_id, student_id, status, users(name, email), "
        "interviews(overall_score_pct), student_profiles(validated_skills, college_name, parsed_resume)"
    ).in_("job_posting_id", job_ids).execute().data

    ranked = sorted(
        applications,
        key=lambda a: (a.get("interviews") or {}).get("overall_score_pct") or 0,
        reverse=True,
    )
    return jsonify(ranked[:10])


@industry_bp.patch("/applications/<application_id>/status")
@token_required
@roles_required(ROLE_INDUSTRY)
def update_application_status(application_id):
    data = request.get_json(force=True) or {}
    new_status = data.get("status")
    if new_status not in {"submitted", "shortlisted", "rejected", "hired"}:
        return jsonify({"error": "invalid status"}), 400

    supabase = get_supabase()
    result = supabase.table("applications").update({"status": new_status}).eq("id", application_id).execute()
    if result.data:
        app_row = result.data[0]
        # Emit to student's room
        socketio.emit("application_status_changed", app_row, to=f"user_{app_row['student_id']}")
        # Emit to industry room and broadcast for realtime sync
        socketio.emit("application_status_changed", app_row, to="industrys_room")
        socketio.emit("application_status_changed", app_row)

        supabase.table("activity_log").insert({
            "user_id": app_row["student_id"], "type": "application_status_changed",
            "title": "Application status updated", "subtitle": f"Status updated to {new_status}",
        }).execute()
        return jsonify(app_row)
    return jsonify({"error": "application not found"}), 404


@industry_bp.post("/ai/ask")
@token_required
@roles_required(ROLE_INDUSTRY)
def ask_ai():
    data = request.get_json(force=True) or {}
    question = data.get("question")
    if not question:
        return jsonify({"error": "question is required"}), 400

    supabase = get_supabase()
    uid = request.user["id"]

    jobs = supabase.table("job_postings").select("title, type, status, required_skills").eq("posted_by", uid).execute().data
    courses = supabase.table("courses").select("title, skills_covered").eq("published_by", uid).execute().data

    context = (
        f"Postings: {'; '.join(j['title'] + ' (' + j['status'] + ') needs ' + ', '.join(j['required_skills']) for j in jobs) or 'none yet'}\n"
        f"Courses published: {'; '.join(c['title'] for c in courses) or 'none yet'}"
    )
    answer = answer_industry_question(context, question, uid)
    return jsonify({"answer": answer})


@industry_bp.get("/me/dashboard")
@token_required
@roles_required(ROLE_INDUSTRY)
def dashboard():
    supabase = get_supabase()
    uid = request.user["id"]

    jobs = supabase.table("job_postings").select("id, title, status").eq("posted_by", uid).execute().data
    job_ids = [j["id"] for j in jobs]

    applications = []
    if job_ids:
        applications = supabase.table("applications").select(
            "id, job_posting_id, status, interviews(overall_score_pct)"
        ).in_("job_posting_id", job_ids).execute().data

    courses = supabase.table("courses").select("id, title").eq("published_by", uid).execute().data
    activity = supabase.table("activity_log").select("*").eq("user_id", uid).order("created_at", desc=True).limit(10).execute().data

    funnel = {
        "open_postings": len([j for j in jobs if j["status"] == "open"]),
        "applications_received": len(applications),
        "shortlisted": len([a for a in applications if a["status"] == "shortlisted"]),
        "hired": len([a for a in applications if a["status"] == "hired"]),
        "avg_interview_score": round(
            sum(a["interviews"]["overall_score_pct"] for a in applications if a.get("interviews")) / len(applications), 1
        ) if applications else 0,
    }

    next_best_action = pick_next_best_action(jobs, applications)
    conversion_rate = compute_conversion_rate(applications)

    return jsonify({
        "jobs": jobs,
        "courses": courses,
        "funnel": funnel,
        "recent_activity": activity,
        "next_best_action": next_best_action,
        "conversion_rate": conversion_rate,
    })
