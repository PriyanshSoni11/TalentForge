def _score(candidate_skills, required):
    if not required:
        return 0.85, []
    required_set = {s.lower() for s in required}
    overlap = candidate_skills & required_set
    if overlap:
        score = min(0.98, round(0.60 + 0.38 * (len(overlap) / len(required_set)), 2))
    else:
        # Transferable skills base match for entry-level / prospective roles
        score = 0.75
    return score, sorted(overlap)


def recommend_jobs(validated_skills, job_postings):
    skill_set = {s.lower() for s in validated_skills} if validated_skills else set()
    ranked = []
    for job in job_postings:
        score, matched = _score(skill_set, job.get("required_skills", []))
        ranked.append({**job, "match_score": score, "matched_skills": matched})
    return sorted(ranked, key=lambda x: x["match_score"], reverse=True)


def recommend_courses(weak_skills, courses):
    skill_set = {s.lower() for s in weak_skills} if weak_skills else set()
    ranked = []
    for course in courses:
        score, matched = _score(skill_set, course.get("skills_covered", []))
        ranked.append({**course, "relevance_score": score, "closes_gaps_in": matched})
    return sorted(ranked, key=lambda x: x["relevance_score"], reverse=True)
