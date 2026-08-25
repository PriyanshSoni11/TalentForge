# TalentForge ✦

An AI-powered career enablement platform connecting students, colleges, and industry recruiters through automated resume skill extraction, interactive AI video interviews, verified skill assessments, and college mentor networks.

---

## ⚡ Tech Stack

- **Backend:** Python (Flask), Eventlet, Flask-SocketIO, PyJWT
- **Database & Storage:** Supabase (PostgreSQL, Storage buckets)
- **AI Engine:** Google Gemini AI (Resume parsing, MCQ generation, multimodal interview evaluation)
- **Frontend:** Vanilla HTML5, Modern CSS / Tailwind CSS, JavaScript (ES6+)

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- A [Supabase](https://supabase.com) project with `resumes` and `interviews` storage buckets
- Google Gemini API Key

### 2. Installation & Setup

```bash
# Clone and enter directory
cd TalentForge

# Create and activate virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the root directory:

```env
SUPABASE_URL=https://your-supabase-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-supabase-service-role-key
JWT_SECRET=your-secure-jwt-secret
GOOGLE_API_KEY=your-gemini-api-key
PORT=5000
DEBUG=True
```

### 4. Run the Application

```bash
python main.py
```

The application will be live at `http://localhost:5000`.

---

## 🧭 Main Portal Routes

| Workspace / View | URL Path | Description |
| :--- | :--- | :--- |
| **Landing Page** | `/` | Home page and role selection |
| **Student Registration** | `/student/register` | Student onboarding with resume upload |
| **Mentor Registration** | `/mentor/register` | Mentor onboarding with college affiliation |
| **Student Dashboard** | `/dashboard/student` | Skill readiness, active applications, learning paths |
| **Student Portfolio** | `/dashboard/student/portfolio` | Living portfolio extracted from resume |
| **Company / Mentor Dashboard** | `/dashboard/industry` | Hiring funnel, applicant conversion, postings |
| **College Student Directory** | `/dashboard/industry/candidates` | Mentor view of students from their college |
| **AI Video Interview** | `/interview/<id>` | Multimodal AI interview workspace |
| **Skill Assessment** | `/assessment` | Dynamic MCQ skill validation |
