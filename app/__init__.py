from flask import Flask, render_template, redirect
from flask import request

from app.config import get_config
from app.extensions import socketio, init_supabase


def create_app():
    app = Flask(__name__)
    app.config.from_mapping(get_config())

    init_supabase(app)
    socketio.init_app(app, message_queue=app.config.get("SOCKETIO_MESSAGE_QUEUE"))

    from app.auth.routes import auth_bp
    from app.students.routes import students_bp
    from app.industry.routes import industry_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(students_bp, url_prefix="/api/students")
    app.register_blueprint(industry_bp, url_prefix="/api/industry")

    from app.sockets import events

    @app.route("/")
    def index():
        return render_template("base.html", title="TalentForge")

    @app.route("/login")
    def login_page():
        return render_template("auth/login.html", role="student", role_label="Student", register_url="/student/register")

    @app.route("/register")
    def register_page():
        return render_template("auth/register.html", role="student", role_label="Student", login_url="/student/login")

    @app.route("/student/login")
    def student_login_page():
        return render_template("auth/login.html", role="student", role_label="Student", register_url="/student/register")

    @app.route("/industry/login")
    def industry_login_page():
        return render_template("auth/login.html", role="industry", role_label="Company", register_url="/industry/register")

    @app.route("/mentor/login")
    def mentor_login_page():
        return render_template("auth/login.html", role="industry", role_label="Mentor", register_url="/mentor/register")

    @app.route("/student/register")
    def student_register_page():
        return render_template("auth/register.html", role="student", role_label="Student", login_url="/student/login")

    @app.route("/industry/register")
    def industry_register_page():
        return render_template("auth/register.html", role="industry", role_label="Company", login_url="/industry/login")

    @app.route("/mentor/register")
    def mentor_register_page():
        return render_template("auth/register.html", role="industry", role_label="Mentor", login_url="/mentor/login")

    @app.route("/assessment")
    def assessment_page():
        return render_template("assessment.html")

    @app.route("/interview/<interview_id>")
    def interview_page(interview_id):
        return render_template("interview.html", interview_id=interview_id)

    @app.route("/practice-interview")
    @app.route("/dashboard/student/practice-interview")
    def practice_interview_redirect():
        return redirect("https://intervueai-landing.vercel.app/", code=302)

    @app.route("/profile")
    def profile_page():
        return render_template("profile.html")

    @app.route("/dashboard/student")
    def student_dashboard_page():
        return render_template("dashboard/student.html")

    @app.route("/dashboard/student/roles")
    def student_roles_page():
        return render_template("dashboard/student_roles.html")

    @app.route("/dashboard/student/courses")
    def student_courses_page():
        return render_template("dashboard/student_courses.html")

    @app.route("/dashboard/student/portfolio")
    def student_portfolio_page():
        return render_template("dashboard/student_portfolio.html")

    @app.route("/dashboard/student/applications")
    def student_applications_page():
        return render_template("dashboard/student_applications.html")

    @app.route("/dashboard/student/mentors")
    def student_mentors_page():
        return render_template("dashboard/student_mentors.html")

    @app.route("/dashboard/student/events")
    def student_events_page():
        return render_template("dashboard/student_events.html")

    @app.route("/dashboard/industry")
    def industry_dashboard_page():
        return render_template("dashboard/industry.html")

    @app.route("/dashboard/industry/candidates")
    def industry_candidates_page():
        return render_template("dashboard/industry_candidates.html")

    @app.route("/dashboard/industry/postings")
    def industry_postings_page():
        return render_template("dashboard/industry_postings.html")

    @app.route("/dashboard/industry/courses")
    def industry_courses_page():
        return render_template("dashboard/industry_courses.html")

    @app.route("/dashboard/industry/network")
    def industry_network_page():
        return render_template("dashboard/industry_network.html")

    @app.route("/dashboard/industry/events")
    def industry_events_page():
        return render_template("dashboard/industry_events.html")

    @app.errorhandler(404)
    def not_found(e):
        return {"error": "not found"}, 404

    return app
