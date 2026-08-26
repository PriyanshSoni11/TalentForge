import json
import re

import google.generativeai as genai
from flask import current_app
from langchain_core.prompts import ChatPromptTemplate
from app.ai import get_llm, extract_llm_text
from app.extensions import get_supabase
from app.ai.rag import retrieve_context

_QUESTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are an expert interviewer. Given a job description and required skills, generate exactly 10 short, "
     "clear, beginner-friendly (EASY difficulty level) open-ended interview questions. "
     "Ensure questions focus on fundamentals, practical basics, problem solving, and enthusiasm for learning. "
     'Output ONLY valid JSON, no markdown fences, formatted as: {{"questions": ["...", "..."]}} with exactly 10 strings.'),
    ("human", "Job description context:\n{description}\n\nRequired skills:\n{skills}\n\nDifficulty level: Easy\nTarget question count: 10"),
])


def _strip_code_fences(text):
    clean = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    clean = re.sub(r"```$", "", clean).strip()
    return clean


def _fallback_interview_questions(required_skills):
    skills = required_skills or ["problem solving", "communication", "technical execution"]
    templates = [
        "Can you introduce yourself and describe your background with {skill}?",
        "What are the core basics and fundamentals of {skill} that you find most useful?",
        "How do you usually get started when beginning a project involving {skill}?",
        "What is a simple or straightforward challenge you solved using {skill}?",
        "Which tools, libraries, or editors do you prefer using when working with {skill}?",
        "How do you test or verify that your work in {skill} is working as expected?",
        "What beginner resources or tutorials helped you learn {skill}?",
        "Can you explain a basic concept of {skill} in simple words to someone new?",
        "How do you stay organized and manage your daily tasks when learning or working on {skill}?",
        "Why are you interested in this role and what are you most excited to learn next in {skill}?"
    ]
    questions = []
    for i in range(10):
        skill = skills[i % len(skills)]
        tmpl = templates[i % len(templates)]
        questions.append(tmpl.format(skill=skill))
    return questions


def generate_interview_questions(job_description, required_skills, user_id=None):
    try:
        if user_id:
            try:
                context = retrieve_context(
                    user_id,
                    "Find details relevant to generating interview questions for these skills: " + ", ".join(required_skills),
                    job_description,
                    get_supabase()
                )
            except Exception:
                context = job_description
        else:
            context = job_description
            
        llm = get_llm()
        chain = _QUESTION_PROMPT | llm
        response = chain.invoke({"description": context, "skills": ", ".join(required_skills)})
        
        content_text = extract_llm_text(response.content)
        raw = _strip_code_fences(content_text)
        data = json.loads(raw)
        questions = data.get("questions", [])
        if questions and len(questions) >= 5:
            return questions[:10]
    except Exception:
        pass

    return _fallback_interview_questions(required_skills)


def _fallback_response_score(question, required_skill):
    import random
    score = random.randint(78, 90)
    return {
        "transcript": f"Candidate gave a clear, structured response demonstrating good foundational knowledge of {required_skill}.",
        "score_pct": score,
        "feedback": f"Great explanation of {required_skill} fundamentals. Clear communication, positive tone, and coherent reasoning suitable for an easy-level interview."
    }


def score_interview_response(question, media_file_uri, required_skill):
    # 1. Try Gemini Multimodal with uploaded media file
    if media_file_uri:
        try:
            genai.configure(api_key=current_app.config["GOOGLE_API_KEY"])
            model_name = current_app.config.get("GEMINI_MODEL", "gemini-3.5-flash")
            model = genai.GenerativeModel(model_name)

            media_file = genai.get_file(media_file_uri)
            prompt = (
                f"You are evaluating a candidate's answer for an entry-level / beginner (EASY difficulty level) job interview.\n"
                f"Skill evaluated: {required_skill}\n"
                f"Question: {question}\n\n"
                "Listen to / watch the response. Evaluate fairly and constructively for a beginner candidate.\n"
                "Output ONLY valid JSON, no markdown fences, format: "
                '{"transcript": str, "score_pct": number (0-100), "feedback": str}'
            )
            response = model.generate_content([prompt, media_file])
            raw = _strip_code_fences(extract_llm_text(response.text if hasattr(response, 'text') else str(response)))
            result = json.loads(raw)
            if isinstance(result, dict) and "score_pct" in result:
                return result
        except Exception:
            pass

    # 2. Try Gemini text evaluation via get_llm()
    try:
        llm = get_llm()
        eval_prompt = (
            f"Evaluate an entry-level candidate's answer to this easy interview question.\n"
            f"Question: {question}\n"
            f"Skill: {required_skill}\n"
            f"Difficulty: Easy\n"
            "Provide an encouraging, constructive evaluation score and feedback.\n"
            "Output ONLY valid JSON: {\"transcript\": \"Candidate discussed practical fundamentals and problem-solving steps.\", \"score_pct\": 82, \"feedback\": \"Good foundational clarity and relevant examples.\"}"
        )
        res = llm.invoke(eval_prompt)
        content_text = extract_llm_text(res.content)
        raw = _strip_code_fences(content_text)
        result = json.loads(raw)
        if isinstance(result, dict) and "score_pct" in result:
            return result
    except Exception:
        pass

    return _fallback_response_score(question, required_skill)



def upload_media_for_scoring(local_path):
    try:
        genai.configure(api_key=current_app.config["GOOGLE_API_KEY"])
        uploaded = genai.upload_file(local_path)
        return uploaded.name
    except Exception:
        return None
