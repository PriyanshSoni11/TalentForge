import json
import re

from langchain_core.prompts import ChatPromptTemplate
from app.ai import get_llm, extract_llm_text
from app.ai.rag import retrieve_context

_GEN_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a technical assessor. Given a list of a candidate's skills, generate EXACTLY "
     "20 multiple-choice questions spread across those skills. "
     "CRITICAL REQUIREMENT: All 20 questions MUST be completely distinct and unique from each other. "
     "Do not repeat concepts or phrasing. Mix difficulty. "
     "Output ONLY valid JSON, no markdown fences, no commentary, shape: "
     '{{"questions": [{{"skill": str, "question": str, "options": [str, str, str, str], '
     '"correct_index": int}}]}} '
     "correct_index is 0-based into options."),
    ("human", "Candidate skills:\n{skills}\n\nRelevant resume context:\n{context}"),
])

_SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a career coach. Given a per-skill correct/incorrect breakdown from a technical "
     "assessment, write a short strengths and weaknesses summary, and score the candidate 0-100 "
     "on three axes inferred from the breakdown: technical (raw correctness), communication "
     "(clarity implied by how well-rounded their correct skills are), and problem_solving (how "
     "well they handle harder/less common skills). Output ONLY valid JSON, no markdown fences, shape: "
     '{{"strengths": [str], "weaknesses": [str], "validated_skills": [str], '
     '"technical_pct": number, "communication_pct": number, "problem_solving_pct": number}} '
     "validated_skills = skills the candidate demonstrably scored well on."),
    ("human", "Per-skill breakdown (skill: correct/total):\n{breakdown}"),
])


def _strip_code_fences(text):
    clean = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    clean = re.sub(r"```$", "", clean).strip()
    return clean


def generate_assessment(skills, context="", owner_id=None, supabase=None):
    if owner_id and supabase and context:
        try:
            context = retrieve_context(
                owner_id,
                "Generate technical assessment questions from this candidate resume",
                context,
                supabase,
            )
        except Exception:
            pass
    try:
        llm = get_llm()
        chain = _GEN_PROMPT | llm
        response = chain.invoke({"skills": ", ".join(skills), "context": context or "No additional context."})
        raw = _strip_code_fences(extract_llm_text(response.content))
    except Exception:
        return _fallback_questions(skills)

    try:
        data = json.loads(raw)
        questions = data.get("questions", [])
    except json.JSONDecodeError:
        questions = []

    return questions[:20] if len(questions) >= 20 else _fallback_questions(skills)



def _fallback_questions(skills):
    skills = skills or ["general skills"]
    questions = []
    for index in range(20):
        skill = skills[index % len(skills)]
        questions.append({
            "skill": skill,
            "question": f"Which approach best demonstrates practical knowledge of {skill}?",
            "options": [
                f"Apply {skill} to a relevant problem and verify the result",
                "Avoid using it and rely on guesswork",
                "Use it without checking the outcome",
                "Remove it from the project",
            ],
            "correct_index": 0,
        })
    return questions


def grade_assessment(questions, answers):
    breakdown = {}
    responses = []

    for q in questions:
        qid = q["id"]
        skill = q["skill_tag"]
        selected = answers.get(str(qid))
        is_correct = selected is not None and int(selected) == q["correct_option"]

        breakdown.setdefault(skill, {"correct": 0, "total": 0})
        breakdown[skill]["total"] += 1
        if is_correct:
            breakdown[skill]["correct"] += 1

        responses.append({"question_id": qid, "selected_option": selected, "is_correct": is_correct})

    total_correct = sum(r["is_correct"] for r in responses)
    score_pct = round((total_correct / len(questions)) * 100, 1) if questions else 0.0

    return {"score_pct": score_pct, "breakdown": breakdown, "responses": responses}


def generate_strengths_weaknesses(breakdown):
    formatted = "\n".join(f"- {skill}: {v['correct']}/{v['total']}" for skill, v in breakdown.items())
    try:
        llm = get_llm()
        chain = _SUMMARY_PROMPT | llm
        response = chain.invoke({"breakdown": formatted})
        raw = _strip_code_fences(extract_llm_text(response.content))
    except Exception:
        return _fallback_summary(breakdown)


    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "strengths": [], "weaknesses": [], "validated_skills": [],
            "technical_pct": None, "communication_pct": None, "problem_solving_pct": None,
        }

    return result if result.get("validated_skills") or result.get("strengths") else _fallback_summary(breakdown)


def _fallback_summary(breakdown):
    validated = [skill for skill, values in breakdown.items() if values["correct"] / max(values["total"], 1) >= 0.7]
    weaknesses = [skill for skill, values in breakdown.items() if skill not in validated]
    technical = round(sum(value["correct"] for value in breakdown.values()) / max(sum(value["total"] for value in breakdown.values()), 1) * 100, 1)
    return {
        "strengths": validated,
        "weaknesses": weaknesses,
        "validated_skills": validated,
        "technical_pct": technical,
        "communication_pct": technical,
        "problem_solving_pct": technical,
    }
