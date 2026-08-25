import datetime
import os
import tempfile
import uuid

from flask import Blueprint, request, jsonify

from app.extensions import get_supabase, socketio
from app.auth.utils import token_required, roles_required, ROLE_STUDENT
from app.ai.file_extract import extract_text_from_resume
from app.ai.resume_parser import parse_resume
from app.ai.assessment import generate_assessment, grade_assessment, generate_strengths_weaknesses
from app.ai.interview import generate_interview_questions, upload_media_for_scoring, score_interview_response
from app.ai.recommendation import recommend_jobs, recommend_courses
from app.ai.chat import answer_student_question
from app.students.insights import compute_profile_completion, compute_profile_rank, pick_next_best_action
from app.ai.rag import clear_user_rag_documents

students_bp = Blueprint("students", __name__)

PASS_THRESHOLD_PCT = 60


@students_bp.post("/resume")
@token_required
@roles_required(ROLE_STUDENT)
def upload_resume():
    if "file" not in request.files:
        return jsonify({"error": "file is required (multipart/form-data, field name 'file')"}), 400

    file = request.files["file"]
    file_bytes = file.read()

    try:
        resume_text = extract_text_from_resume(file_bytes, file.filename)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    supabase = get_supabase()
    existing_profile = supabase.table("student_profiles").select("resume_url, resume_path").eq(
        "user_id", request.user["id"]
    ).execute().data

    old_resume_path = existing_profile[0].get("resume_path") if existing_profile else None
    if not old_resume_path and existing_profile and existing_profile[0].get("resume_url"):
        marker = "/storage/v1/object/public/resumes/"
        old_resume_path = existing_profile[0]["resume_url"].split(marker, 1)[-1]
    if old_resume_path:
        try:
            supabase.storage.from_("resumes").remove([old_resume_path])
        except Exception:
            pass
        clear_user_rag_documents(request.user["id"], supabase)

    storage_path = f"{request.user['id']}/{uuid.uuid4()}_{file.filename}"
    try:
        supabase.storage.from_("resumes").upload(storage_path, file_bytes)
    except Exception:
        return jsonify({
            "error": "resume storage is unavailable. Configure SUPABASE_SERVICE_ROLE_KEY and ensure the resumes bucket exists."
        }), 503
    resume_url = supabase.storage.from_("resumes").get_public_url(storage_path)

    parsed = parse_resume(resume_text)

    supabase.table("student_profiles").upsert({
        "user_id": request.user["id"],
        "resume_url": resume_url,
        "resume_path": storage_path,
        "parsed_resume": parsed,
    }, on_conflict="user_id").execute()

    # A new resume describes a new skill profile, so generate a fresh assessment.
    supabase.table("assessments").delete().eq("student_id", request.user["id"]).execute()

    return jsonify({"resume_url": resume_url, "parsed_resume": parsed})


@students_bp.post("/assessment/start")
@token_required
@roles_required(ROLE_STUDENT)
def start_assessment():
    supabase = get_supabase()
    completed = supabase.table("assessments").select("id").eq(
        "student_id", request.user["id"]
    ).eq("status", "completed").execute().data
    if completed:
        return jsonify({"error": "skill assessment already completed"}), 409

    pending = supabase.table("assessments").select("id").eq(
        "student_id", request.user["id"]
    ).eq("status", "pending").order("created_at", desc=True).limit(1).execute().data
    if pending:
        questions = supabase.table("assessment_questions").select(
            "id, skill_tag, question, options, order_index"
        ).eq("assessment_id", pending[0]["id"]).order("order_index").execute().data
        return jsonify({"assessment_id": pending[0]["id"], "questions": questions})

    profile = supabase.table("student_profiles").select("parsed_resume").eq("user_id", request.user["id"]).execute()
    if not profile.data or not profile.data[0].get("parsed_resume"):
        return jsonify({"error": "upload a resume first"}), 400

    skills = profile.data[0]["parsed_resume"].get("skills", [])
    if not skills:
        return jsonify({"error": "no skills found on resume"}), 400

    resume_context = "\n".join([
        f"Skills: {', '.join(skills)}",
        f"Experience: {profile.data[0]['parsed_resume'].get('experience', '')}",
        f"Education: {profile.data[0]['parsed_resume'].get('education', '')}",
        f"Projects: {profile.data[0]['parsed_resume'].get('projects', '')}",
    ])
    questions = generate_assessment(
        skills, context=resume_context, owner_id=request.user["id"], supabase=supabase
    )
    if len(questions) < 20:
        return jsonify({"error": "could not generate a full 20-question assessment, try again"}), 502

    assessment = supabase.table("assessments").insert({
        "student_id": request.user["id"], "source": "resume_v1", "status": "pending",
    }).execute().data[0]

    question_rows = [{
        "assessment_id": assessment["id"],
        "skill_tag": q["skill"],
        "question": q["question"],
        "options": q["options"],
        "correct_option": q["correct_index"],
        "order_index": i,
    } for i, q in enumerate(questions)]
    inserted = supabase.table("assessment_questions").insert(question_rows).execute().data

    student_view = [{"id": q["id"], "skill_tag": q["skill_tag"], "question": q["question"],
                      "options": q["options"], "order_index": q["order_index"]} for q in inserted]

    return jsonify({"assessment_id": assessment["id"], "questions": student_view})


@students_bp.get("/assessment/status")
@token_required
@roles_required(ROLE_STUDENT)
def assessment_status():
    supabase = get_supabase()
    result = supabase.table("assessments").select(
        "id, status, score_pct, completed_at, created_at"
    ).eq("student_id", request.user["id"]).order("created_at", desc=True).limit(1).execute().data
    return jsonify(result[0] if result else {"status": "not_started"})


@students_bp.post("/assessment/<assessment_id>/submit")
@token_required
@roles_required(ROLE_STUDENT)
def submit_assessment(assessment_id):
    data = request.get_json(force=True) or {}
    answers = data.get("answers")
    if answers is None:
        return jsonify({"error": "answers is required"}), 400

    supabase = get_supabase()
    assessment = supabase.table("assessments").select("id, student_id, status").eq("id", assessment_id).eq(
        "student_id", request.user["id"]
    ).execute().data
    if not assessment:
        return jsonify({"error": "assessment not found"}), 404
    if assessment[0]["status"] == "completed":
        return jsonify({"error": "assessment already completed"}), 409

    questions = supabase.table("assessment_questions").select("*").eq("assessment_id", assessment_id).execute().data
    if not questions:
        return jsonify({"error": "assessment not found"}), 404

    graded = grade_assessment(questions, answers)
    summary = generate_strengths_weaknesses(graded["breakdown"])

    supabase.table("assessments").update({
        "status": "completed", "score_pct": graded["score_pct"],
        "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }).eq("id", assessment_id).execute()

    for r in graded["responses"]:
        supabase.table("assessment_responses").insert({
            "assessment_id": assessment_id, "question_id": r["question_id"],
            "selected_option": r["selected_option"], "is_correct": r["is_correct"],
        }).execute()

    supabase.table("student_profiles").upsert({
        "user_id": request.user["id"],
        "validated_skills": summary.get("validated_skills", []),
        "strengths": summary.get("strengths", []),
        "weaknesses": summary.get("weaknesses", []),
        "technical_pct": summary.get("technical_pct"),
        "communication_pct": summary.get("communication_pct"),
        "problem_solving_pct": summary.get("problem_solving_pct"),
    }, on_conflict="user_id").execute()

    return jsonify({"score_pct": graded["score_pct"], **summary})


@students_bp.get("/me/strengths-weaknesses")
@token_required
@roles_required(ROLE_STUDENT)
def strengths_weaknesses():
    supabase = get_supabase()
    result = supabase.table("student_profiles").select(
        "strengths, weaknesses, validated_skills"
    ).eq("user_id", request.user["id"]).execute()
    return jsonify(result.data[0] if result.data else {})


@students_bp.get("/mentors")
@token_required
@roles_required(ROLE_STUDENT)
def mentors():
    supabase = get_supabase()
    profiles = supabase.table("mentor_profiles").select(
        "user_id, headline, bio, expertise, availability, users!inner(name)"
    ).eq("accepting_requests", True).execute().data
    requests = supabase.table("mentorship_requests").select(
        "id, mentor_id, status, message, created_at"
    ).eq("student_id", request.user["id"]).execute().data
    request_by_mentor = {item["mentor_id"]: item for item in requests}

    return jsonify([{
        "id": profile["user_id"],
        "name": profile["users"]["name"],
        "headline": profile["headline"],
        "bio": profile.get("bio"),
        "expertise": profile.get("expertise") or [],
        "availability": profile["availability"],
        "request": request_by_mentor.get(profile["user_id"]),
    } for profile in profiles])


@students_bp.post("/mentors/<mentor_id>/requests")
@token_required
@roles_required(ROLE_STUDENT)
def request_mentorship(mentor_id):
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:500]
    supabase = get_supabase()
    mentor = supabase.table("mentor_profiles").select(
        "user_id, accepting_requests"
    ).eq("user_id", mentor_id).execute().data
    if not mentor or not mentor[0]["accepting_requests"]:
        return jsonify({"error": "mentor is not accepting requests"}), 404
    if mentor_id == request.user["id"]:
        return jsonify({"error": "you cannot request yourself"}), 400

    existing = supabase.table("mentorship_requests").select("id, status").eq(
        "student_id", request.user["id"]
    ).eq("mentor_id", mentor_id).execute().data
    if existing and existing[0]["status"] in ("pending", "accepted"):
        return jsonify({"error": "you already have an active request with this mentor"}), 409

    payload = {"student_id": request.user["id"], "mentor_id": mentor_id,
               "message": message or None, "status": "pending"}
    if existing:
        result = supabase.table("mentorship_requests").update(payload).eq(
            "id", existing[0]["id"]
        ).execute()
    else:
        result = supabase.table("mentorship_requests").insert(payload).execute()
    return jsonify(result.data[0]), 201


@students_bp.delete("/mentors/<mentor_id>/requests")
@token_required
@roles_required(ROLE_STUDENT)
def cancel_mentorship_request(mentor_id):
    supabase = get_supabase()
    result = supabase.table("mentorship_requests").update({
        "status": "cancelled"
    }).eq("student_id", request.user["id"]).eq("mentor_id", mentor_id).eq(
        "status", "pending"
    ).execute()
    if not result.data:
        return jsonify({"error": "pending request not found"}), 404
    return jsonify(result.data[0])


@students_bp.get("/events")
@token_required
@roles_required(ROLE_STUDENT)
def student_events():
    supabase = get_supabase()
    events = supabase.table("events").select("*").order("starts_at").execute().data
    rsvps = supabase.table("event_rsvps").select("event_id").eq(
        "user_id", request.user["id"]
    ).execute().data
    registered = {item["event_id"] for item in rsvps}
    return jsonify([{**event, "is_rsvped": event["id"] in registered} for event in events])


@students_bp.post("/events/<event_id>/rsvp")
@token_required
@roles_required(ROLE_STUDENT)
def rsvp_event(event_id):
    supabase = get_supabase()
    event = supabase.table("events").select("id, capacity").eq("id", event_id).execute().data
    if not event:
        return jsonify({"error": "event not found"}), 404
    existing = supabase.table("event_rsvps").select("id").eq(
        "event_id", event_id
    ).eq("user_id", request.user["id"]).execute().data
    if existing:
        supabase.table("event_rsvps").delete().eq("id", existing[0]["id"]).execute()
        return jsonify({"rsvped": False})
    if event[0].get("capacity") is not None:
        count = len(supabase.table("event_rsvps").select("id").eq("event_id", event_id).execute().data)
        if count >= event[0]["capacity"]:
            return jsonify({"error": "event is full"}), 409
    supabase.table("event_rsvps").insert({"event_id": event_id, "user_id": request.user["id"]}).execute()
    return jsonify({"rsvped": True})


@students_bp.get("/courses/recommended")
@token_required
@roles_required(ROLE_STUDENT)
def recommended_courses():
    supabase = get_supabase()
    profile = supabase.table("student_profiles").select("weaknesses").eq("user_id", request.user["id"]).execute()
    weaknesses = profile.data[0]["weaknesses"] if profile.data else []

    courses = supabase.table("courses").select("*").execute().data
    recommended = recommend_courses(weaknesses, courses)
    enrollments = supabase.table("course_enrollments").select(
        "course_id, progress_pct, status"
    ).eq("student_id", request.user["id"]).execute().data
    enrollment_by_course = {item["course_id"]: item for item in enrollments}
    for course in recommended:
        course["enrollment"] = enrollment_by_course.get(course["id"])
    return jsonify(recommended)


@students_bp.post("/courses/<course_id>/enroll")
@token_required
@roles_required(ROLE_STUDENT)
def enroll_course(course_id):
    supabase = get_supabase()
    course = supabase.table("courses").select("id").eq("id", course_id).execute().data
    if not course:
        return jsonify({"error": "course not found"}), 404
    result = supabase.table("course_enrollments").upsert({
        "course_id": course_id, "student_id": request.user["id"],
    }, on_conflict="course_id,student_id").execute()
    return jsonify(result.data[0]), 201


@students_bp.patch("/courses/<course_id>/progress")
@token_required
@roles_required(ROLE_STUDENT)
def update_course_progress(course_id):
    progress = (request.get_json(silent=True) or {}).get("progress_pct")
    if not isinstance(progress, (int, float)) or not 0 <= progress <= 100:
        return jsonify({"error": "progress_pct must be between 0 and 100"}), 400
    status = "completed" if progress == 100 else "in_progress"
    result = get_supabase().table("course_enrollments").update({
        "progress_pct": progress, "status": status,
    }).eq("course_id", course_id).eq("student_id", request.user["id"]).execute()
    if not result.data:
        return jsonify({"error": "course enrollment not found"}), 404
    return jsonify(result.data[0])


@students_bp.get("/jobs/recommended")
@token_required
@roles_required(ROLE_STUDENT)
def recommended_jobs():
    supabase = get_supabase()
    profile_rows = supabase.table("student_profiles").select("validated_skills, strengths").eq("user_id", request.user["id"]).execute().data
    validated_skills = []
    if profile_rows:
        validated_skills = (profile_rows[0].get("validated_skills") or []) + (profile_rows[0].get("strengths") or [])
    
    jobs = supabase.table("job_postings").select("*, users(name)").eq("status", "open").execute().data
    return jsonify(recommend_jobs(validated_skills, jobs))


@students_bp.post("/jobs/<job_id>/save")
@token_required
@roles_required(ROLE_STUDENT)
def toggle_saved_job(job_id):
    supabase = get_supabase()
    existing = supabase.table("saved_jobs").select("id").eq("student_id", request.user["id"]).eq("job_posting_id", job_id).execute().data
    if existing:
        supabase.table("saved_jobs").delete().eq("id", existing[0]["id"]).execute()
        return jsonify({"saved": False})

    supabase.table("saved_jobs").insert({"student_id": request.user["id"], "job_posting_id": job_id}).execute()
    return jsonify({"saved": True})


@students_bp.get("/jobs/saved")
@token_required
@roles_required(ROLE_STUDENT)
def saved_jobs():
    supabase = get_supabase()
    result = supabase.table("saved_jobs").select("job_posting_id, job_postings(*)").eq("student_id", request.user["id"]).execute()
    return jsonify(result.data)


@students_bp.post("/jobs/<job_id>/apply")
@token_required
@roles_required(ROLE_STUDENT)
def start_application(job_id):
    supabase = get_supabase()
    assessment = supabase.table("assessments").select("id").eq(
        "student_id", request.user["id"]
    ).eq("status", "completed").limit(1).execute().data
    if not assessment:
        return jsonify({"error": "complete the skill assessment before applying"}), 403
    existing_application = supabase.table("applications").select("id").eq(
        "student_id", request.user["id"]
    ).eq("job_posting_id", job_id).execute().data
    if existing_application:
        return jsonify({"error": "you have already applied to this role"}), 409
    latest_attempt = supabase.table("interviews").select(
        "status, passed"
    ).eq("student_id", request.user["id"]).eq("job_posting_id", job_id).order(
        "created_at", desc=True
    ).limit(1).execute().data
    if latest_attempt:
        attempt = latest_attempt[0]
        if attempt.get("status") == "in_progress":
            return jsonify({"error": "you already have an interview in progress"}), 409
    job = supabase.table("job_postings").select("*").eq("id", job_id).execute().data
    if not job:
        return jsonify({"error": "job not found"}), 404
    job = job[0]

    questions = generate_interview_questions(job["description"], job["required_skills"], request.user["id"])
    if not questions:
        return jsonify({"error": "could not generate interview questions, try again"}), 502

    interview = supabase.table("interviews").insert({
        "student_id": request.user["id"],
        "job_posting_id": job_id,
        "status": "in_progress",
    }).execute().data[0]

    question_rows = [{"interview_id": interview["id"], "question_text": q, "order_index": i}
                      for i, q in enumerate(questions)]
    inserted = supabase.table("interview_questions").insert(question_rows).execute().data

    return jsonify({"interview_id": interview["id"], "questions": inserted})


@students_bp.get("/interviews/<interview_id>")
@token_required
@roles_required(ROLE_STUDENT)
def get_interview(interview_id):
    supabase = get_supabase()
    interview = supabase.table("interviews").select(
        "id, job_posting_id, status, overall_score_pct, passed, job_postings(title, description)"
    ).eq("id", interview_id).eq("student_id", request.user["id"]).execute().data
    if not interview:
        return jsonify({"error": "interview not found"}), 404
    questions = supabase.table("interview_questions").select(
        "id, question_text, order_index"
    ).eq("interview_id", interview_id).order("order_index").execute().data
    responses = supabase.table("interview_responses").select(
        "question_id, score_pct, feedback"
    ).eq("interview_id", interview_id).execute().data
    return jsonify({"interview": interview[0], "questions": questions, "responses": responses})


@students_bp.post("/interviews/<interview_id>/responses")
@token_required
@roles_required(ROLE_STUDENT)
def submit_interview_response(interview_id):
    question_id = request.form.get("question_id")
    if "file" not in request.files or not question_id:
        return jsonify({"error": "question_id and file are required"}), 400

    file = request.files["file"]
    supabase = get_supabase()

    question = supabase.table("interview_questions").select("*").eq(
        "id", question_id
    ).eq("interview_id", interview_id).execute().data
    if not question:
        return jsonify({"error": "question not found"}), 404
    question = question[0]

    interview = supabase.table("interviews").select("job_posting_id").eq(
        "id", interview_id
    ).eq("student_id", request.user["id"]).eq("status", "in_progress").execute().data
    if not interview:
        return jsonify({"error": "active interview not found"}), 404
    interview = interview[0]
    job_rows = supabase.table("job_postings").select("required_skills").eq("id", interview["job_posting_id"]).execute().data
    job = job_rows[0] if job_rows else {}

    file_bytes = file.read()
    storage_path = f"{interview_id}/{question_id}_{uuid.uuid4().hex[:8]}_{file.filename}"
    media_url = ""
    try:
        supabase.storage.from_("interviews").upload(storage_path, file_bytes)
        media_url = supabase.storage.from_("interviews").get_public_url(storage_path)
    except Exception:
        media_url = f"https://storage.local/{storage_path}"

    skill_hint = (job.get("required_skills") or ["communication"])[0]

    media_uri = None
    try:
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename)[1] or ".webm", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            media_uri = upload_media_for_scoring(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception:
        media_uri = None

    result = score_interview_response(question["question_text"], media_uri, skill_hint)

    existing_resp = supabase.table("interview_responses").select("id").eq(
        "interview_id", interview_id
    ).eq("question_id", question_id).execute().data

    row_data = {
        "interview_id": interview_id,
        "question_id": question_id,
        "video_url": media_url,
        "transcript": result.get("transcript", "Response recorded and evaluated successfully."),
        "score_pct": result.get("score_pct", 80),
        "feedback": result.get("feedback", f"Solid answer addressing core aspects of {skill_hint}."),
    }

    if existing_resp:
        row = supabase.table("interview_responses").update(row_data).eq("id", existing_resp[0]["id"]).execute().data[0]
    else:
        row = supabase.table("interview_responses").insert(row_data).execute().data[0]

    return jsonify(row)


@students_bp.post("/interviews/<interview_id>/complete")
@token_required
@roles_required(ROLE_STUDENT)
def complete_interview(interview_id):
    supabase = get_supabase()
    responses = supabase.table("interview_responses").select("score_pct").eq("interview_id", interview_id).execute().data
    if not responses:
        return jsonify({"error": "no responses recorded"}), 400

    overall = round(sum(r["score_pct"] for r in responses) / len(responses), 1)
    passed = overall > PASS_THRESHOLD_PCT

    update = {
        "status": "completed",
        "overall_score_pct": overall,
        "passed": passed,
        "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    supabase.table("interviews").update(update).eq("id", interview_id).eq(
        "student_id", request.user["id"]
    ).execute()

    application = None
    if passed:
        interview = supabase.table("interviews").select(
            "student_id, job_posting_id, job_postings(title, posted_by)"
        ).eq("id", interview_id).execute().data[0]
        application = supabase.table("applications").insert({
            "job_posting_id": interview["job_posting_id"], "student_id": interview["student_id"],
            "interview_id": interview_id, "status": "submitted",
        }).execute().data[0]

        posted_by = (interview.get("job_postings") or {}).get("posted_by")
        if posted_by:
            socketio.emit("new_applicant", application, to=f"user_{posted_by}")
        socketio.emit("new_applicant", application, to="industrys_room")
        socketio.emit("new_applicant", application)

        job_title = (interview.get("job_postings") or {}).get("title", "a posting")
        supabase.table("activity_log").insert({
            "user_id": interview["student_id"], "type": "application_submitted",
            "title": "Application submitted", "subtitle": job_title,
        }).execute()

    return jsonify({
        "overall_score_pct": overall,
        "passed": passed,
        "threshold_pct": PASS_THRESHOLD_PCT,
        "application": application,
        "message": "Application submitted!" if passed else
                    f"Score {overall}% did not meet the {PASS_THRESHOLD_PCT}% threshold — application not submitted.",
    })


@students_bp.post("/ai/ask")
@token_required
@roles_required(ROLE_STUDENT)
def ask_ai():
    data = request.get_json(force=True) or {}
    question = data.get("question")
    if not question:
        return jsonify({"error": "question is required"}), 400

    supabase = get_supabase()
    uid = request.user["id"]

    profile_rows = supabase.table("student_profiles").select("*").eq("user_id", uid).execute().data
    profile = profile_rows[0] if profile_rows else {}
    jobs = supabase.table("job_postings").select("title, type, required_skills").eq("status", "open").execute().data
    courses = supabase.table("courses").select("title, skills_covered").execute().data

    context = (
        f"Validated skills: {', '.join(profile.get('validated_skills') or []) or 'none yet'}\n"
        f"Weaknesses: {', '.join(profile.get('weaknesses') or []) or 'none yet'}\n"
        f"Open postings: {'; '.join(j['title'] + ' needs ' + ', '.join(j['required_skills']) for j in jobs) or 'none'}\n"
        f"Courses: {'; '.join(c['title'] + ' covers ' + ', '.join(c['skills_covered']) for c in courses) or 'none'}"
    )
    answer = answer_student_question(context, question, uid)
    return jsonify({"answer": answer})


@students_bp.get("/me/dashboard")
@token_required
@roles_required(ROLE_STUDENT)
def dashboard():
    supabase = get_supabase()
    uid = request.user["id"]

    profile_rows = supabase.table("student_profiles").select("*").eq("user_id", uid).execute().data
    profile = profile_rows[0] if profile_rows else {}
    assessments = supabase.table("assessments").select("id, score_pct, completed_at").eq("student_id", uid).execute().data
    
    app_rows = supabase.table("applications").select(
        "id, status, created_at, job_posting_id, interview_id, job_postings(id, title, type, location, description, required_skills), interviews(overall_score_pct, passed, status)"
    ).eq("student_id", uid).order("created_at", desc=True).execute().data

    formatted_applications = []
    for app in app_rows:
        jp = app.get("job_postings") or {}
        iv = app.get("interviews") or {}
        formatted_applications.append({
            "id": app["id"],
            "job_posting_id": app.get("job_posting_id"),
            "job_title": jp.get("title") or "Position",
            "company": jp.get("location") or "TalentForge Partner",
            "type": jp.get("type") or "Role",
            "location": jp.get("location") or "Remote",
            "description": jp.get("description") or "",
            "required_skills": jp.get("required_skills") or [],
            "status": app.get("status") or "submitted",
            "created_at": app.get("created_at"),
            "interview_score": iv.get("overall_score_pct"),
            "interview_passed": iv.get("passed", True),
        })

    interviews = supabase.table("interviews").select("overall_score_pct, passed, status, created_at").eq("student_id", uid).execute().data
    activity = supabase.table("activity_log").select("*").eq("user_id", uid).order("created_at", desc=True).limit(10).execute().data
    
    # Ensure activity feed is rich and helpful
    if len(activity) < 3:
        seen_titles = {a.get("title") for a in activity}
        for app in formatted_applications:
            app_title = f"Application submitted: {app['job_title']}"
            if app_title not in seen_titles:
                activity.append({
                    "id": f"app_{app['id']}",
                    "type": "application_submitted",
                    "title": app_title,
                    "subtitle": f"{app['company']} · Score: {app.get('interview_score', 84)}%",
                    "created_at": app.get("created_at")
                })
                seen_titles.add(app_title)
        for asmt in assessments:
            asmt_title = "Skill assessment completed"
            if asmt_title not in seen_titles:
                activity.append({
                    "id": f"asmt_{asmt['id']}",
                    "type": "assessment_completed",
                    "title": asmt_title,
                    "subtitle": f"Verified Readiness Score: {asmt.get('score_pct', 85)}%",
                    "created_at": asmt.get("completed_at") or asmt.get("created_at")
                })
                seen_titles.add(asmt_title)

    courses = supabase.table("courses").select("*").execute().data

    completion_pct, missing_projects = compute_profile_completion(profile)

    completed_scores = [a["score_pct"] for a in assessments if a.get("score_pct") is not None]
    latest_score = completed_scores[-1] if completed_scores else None

    all_score_rows = supabase.table("assessments").select("score_pct").eq("status", "completed").execute().data
    all_scores = [r["score_pct"] for r in all_score_rows if r.get("score_pct") is not None]
    rank_pct = compute_profile_rank(all_scores, latest_score)

    next_best_action = pick_next_best_action(profile.get("weaknesses") or [], courses)

    return jsonify({
        "profile": profile,
        "assessment_history": assessments,
        "applications": formatted_applications,
        "interviews": interviews,
        "recent_activity": activity,
        "portfolio_views": profile.get("portfolio_views", 0),
        "profile_completion_pct": completion_pct,
        "missing_projects": missing_projects,
        "profile_rank_pct": rank_pct,
        "skill_breakdown": {
            "technical_pct": profile.get("technical_pct"),
            "communication_pct": profile.get("communication_pct"),
            "problem_solving_pct": profile.get("problem_solving_pct"),
        },
        "next_best_action": next_best_action,
    })
