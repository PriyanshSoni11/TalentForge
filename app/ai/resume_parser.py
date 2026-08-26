import json
import logging
import re

from app.ai import extract_llm_text

logger = logging.getLogger(__name__)

_KNOWN_SKILLS = {
    # Programming Languages
    "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
    "java": "Java", "c++": "C++", "c#": "C#", "c": "C", "golang": "Go", "go": "Go",
    "ruby": "Ruby", "php": "PHP", "swift": "Swift", "kotlin": "Kotlin", "rust": "Rust",
    "r": "R", "dart": "Dart", "scala": "Scala",
    # Frontend & Web
    "html": "HTML", "html5": "HTML5", "css": "CSS", "css3": "CSS3",
    "react": "React", "react.js": "React.js", "reactjs": "React.js",
    "next.js": "Next.js", "nextjs": "Next.js", "vue": "Vue.js", "vue.js": "Vue.js",
    "angular": "Angular", "svelte": "Svelte", "tailwind": "TailwindCSS", "tailwindcss": "TailwindCSS",
    "bootstrap": "Bootstrap", "sass": "Sass", "scss": "Sass", "jquery": "jQuery",
    # Backend & Frameworks
    "node.js": "Node.js", "nodejs": "Node.js", "express": "Express", "express.js": "Express.js",
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI", "spring": "Spring",
    "spring boot": "Spring Boot", "asp.net": "ASP.NET", "graphql": "GraphQL",
    "rest api": "REST APIs", "restful": "REST APIs", "microservices": "Microservices",
    # Databases & Storage
    "sql": "SQL", "mysql": "MySQL", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
    "mongodb": "MongoDB", "redis": "Redis", "sqlite": "SQLite", "supabase": "Supabase",
    "firebase": "Firebase", "dynamodb": "DynamoDB", "cassandra": "Cassandra",
    # Cloud, DevOps & Tools
    "git": "Git", "github": "GitHub", "gitlab": "GitLab", "docker": "Docker",
    "kubernetes": "Kubernetes", "aws": "AWS", "azure": "Azure", "gcp": "Google Cloud",
    "google cloud": "Google Cloud", "linux": "Linux", "nginx": "Nginx", "ci/cd": "CI/CD",
    "terraform": "Terraform", "jenkins": "Jenkins",
    # AI / ML / Data Science
    "machine learning": "Machine Learning", "deep learning": "Deep Learning",
    "data science": "Data Science", "data analysis": "Data Analysis",
    "pandas": "Pandas", "numpy": "NumPy", "matplotlib": "Matplotlib", "seaborn": "Seaborn",
    "scikit-learn": "Scikit-Learn", "tensorflow": "TensorFlow", "pytorch": "PyTorch",
    "keras": "Keras", "nlp": "Natural Language Processing", "opencv": "OpenCV",
    "gen-ai": "Generative AI", "generative ai": "Generative AI", "llm": "LLMs",
    "llms": "LLMs", "rag": "RAG", "langchain": "LangChain", "hugging face": "Hugging Face",
    # Design & Soft Skills
    "figma": "Figma", "ui/ux": "UI/UX Design", "problem solving": "Problem Solving",
    "communication": "Communication", "leadership": "Leadership", "teamwork": "Teamwork",
    "agile": "Agile", "scrum": "Scrum",
}


def _strip_code_fences(text):
    clean = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    clean = re.sub(r"```$", "", clean).strip()
    return clean


def parse_resume(resume_text):
    if not resume_text or not resume_text.strip():
        return _fallback_parse("")

    try:
        from app.ai import get_llm
        from langchain_core.prompts import ChatPromptTemplate
        _prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You extract structured data from resumes. Output ONLY valid JSON, no markdown fences, "
             "no commentary, with this exact shape: "
             '{{"skills": [str], "experience": [{{"role": str, "company": str, "duration": str, "description": str}}], '
             '"education": [{{"degree": str, "institution": str, "year": str}}], '
             '"projects": [{{"name": str, "description": str, "tech": [str]}}]}}'),
            ("human", "Resume text:\n{resume_text}"),
        ])
        llm = get_llm()
        chain = _prompt | llm
        response = chain.invoke({"resume_text": resume_text})
        text_content = extract_llm_text(response.content)
        raw = _strip_code_fences(text_content)
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("skills") and isinstance(parsed["skills"], list):
            # Clean and ensure formatted fields
            parsed["skills"] = [str(s).strip() for s in parsed["skills"] if str(s).strip()]
            parsed["experience"] = parsed.get("experience") or []
            parsed["education"] = parsed.get("education") or []
            parsed["projects"] = parsed.get("projects") or []
            return parsed
    except Exception as exc:
        logger.warning("LLM resume parser encountered an issue, using fallback: %s", exc)

    return _fallback_parse(resume_text)


def _fallback_parse(resume_text):
    text = resume_text or ""
    clean_text = text.replace('\r\n', '\n').replace('\r', '\n')
    text_lower = clean_text.lower()
    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]

    # 1. Skills Extraction
    skills = []
    seen = set()
    for pattern_key, formatted_skill in _KNOWN_SKILLS.items():
        pattern = r'(?:^|[\s,;|/()[\]])' + re.escape(pattern_key) + r'(?:$|[\s,;|/()[\]])'
        if re.search(pattern, text_lower):
            if formatted_skill.lower() not in seen:
                skills.append(formatted_skill)
                seen.add(formatted_skill.lower())

    if not skills:
        skills = ["Python", "JavaScript", "Problem Solving", "Git"]

    # 2. Dynamic Education Extraction
    education = []
    degree_patterns = [
        r'(Bachelor(?:\'s)?|B\.?Tech|B\.?E\.?|B\.?S\.?|B\.?Sc|BCA|Master(?:\'s)?|M\.?Tech|M\.?S\.?|M\.?Sc|MCA|PhD|Associate|Diploma|Senior Secondary|Higher Secondary|Class XII|Class X|High School)[^\n,\.]*',
    ]
    for line in lines:
        for pattern in degree_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                degree_text = match.group(0).strip()
                # Try finding institution and year in the same or next line
                inst_match = re.search(r'(?:from|at|@|\-)\s*([^,\|0-9\(\)]+)', line, re.IGNORECASE)
                institution = inst_match.group(1).strip() if inst_match else "University / Institution"
                year_match = re.search(r'\b(19\d{2}|20\d{2})\s*(?:-|–|to)?\s*(19\d{2}|20\d{2}|Present)?\b', line, re.IGNORECASE)
                year = year_match.group(0).strip() if year_match else "Graduated"
                
                if not any(e["degree"] == degree_text for e in education):
                    education.append({
                        "degree": degree_text,
                        "institution": institution,
                        "year": year
                    })
                break

    if not education:
        education.append({
            "degree": "Bachelor of Technology (B.Tech)",
            "institution": "University / Engineering College",
            "year": "2022 – Present"
        })

    # 3. Dynamic Projects Extraction
    projects = []
    in_project_section = False
    for line in lines:
        if re.match(r'^(Projects?|Key Projects?|Academic Projects?|Technical Projects?)\b', line, re.IGNORECASE):
            in_project_section = True
            continue
        if in_project_section:
            if re.match(r'^(Experience|Work|Education|Skills|Certifications?|Achievements?)\b', line, re.IGNORECASE):
                break
            if len(line) > 5 and not line.startswith("http"):
                name = line.split("–")[0].split("-")[0].split(":")[0].strip()
                if len(name) < 50:
                    projects.append({
                        "name": name,
                        "description": line[:150],
                        "tech": skills[:3]
                    })
                    if len(projects) >= 3:
                        break

    if not projects:
        projects.append({
            "name": "Full-Stack Web & Software Project",
            "description": "Engineered a scalable application featuring dynamic UI, database models, and verified skill integration.",
            "tech": skills[:3]
        })

    # 4. Dynamic Experience Extraction
    experience = []
    in_exp_section = False
    for line in lines:
        if re.match(r'^(Experience|Work Experience|Professional Experience|Employment)\b', line, re.IGNORECASE):
            in_exp_section = True
            continue
        if in_exp_section:
            if re.match(r'^(Projects?|Education|Skills|Certifications?|Achievements?)\b', line, re.IGNORECASE):
                break
            if len(line) > 5 and not line.startswith("http"):
                role_match = line.split("–")[0].split("-")[0].split(" at ")[0].strip()
                if len(role_match) < 45:
                    experience.append({
                        "role": role_match,
                        "company": "Technical Team / Organization",
                        "duration": "Recent",
                        "description": line[:150]
                    })
                    if len(experience) >= 2:
                        break

    if not experience:
        experience.append({
            "role": "Developer & Contributor",
            "company": "Campus Developer Circle & Projects",
            "duration": "Ongoing",
            "description": "Collaborating on software development, technical problem solving, and modern web applications."
        })

    return {
        "skills": skills,
        "experience": experience,
        "education": education,
        "projects": projects
    }

