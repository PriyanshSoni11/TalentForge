def compute_profile_completion(profile):
    projects = (profile.get("parsed_resume") or {}).get("projects", [])
    checks_and_weights = [
        (bool(profile.get("resume_url")), 25),
        (bool(profile.get("college_name")), 15),
        (bool(profile.get("validated_skills")), 25),
        (len(projects) >= 2, 35),
    ]
    pct = sum(weight for done, weight in checks_and_weights if done)
    missing_projects = max(0, 2 - len(projects))
    return pct, missing_projects


def compute_profile_rank(all_scores, my_score):
    if not all_scores or my_score is None:
        return None
    better_or_equal = sum(1 for score in all_scores if score >= my_score)
    percentile = round((better_or_equal / len(all_scores)) * 100)
    return max(percentile, 1)


def pick_next_best_action(weaknesses, courses):
    if not courses:
        return {
            "title": "Explore Open Roles",
            "detail": "Browse curated internships and job openings matching your profile.",
            "course_id": None,
        }

    weak_skill = weaknesses[0] if weaknesses else None
    if weak_skill:
        for course in courses:
            skills_covered = course.get("skills_covered", [])
            if any(weak_skill.lower() in s.lower() for s in skills_covered):
                return {
                    "title": f"Level up your {weak_skill}",
                    "detail": f"Close your biggest skill gap with {course['title']}.",
                    "course_id": course["id"],
                }

    first_course = courses[0]
    return {
        "title": f"Master {first_course['title']}",
        "detail": f"Expand your technical skills with {first_course['title']}.",
        "course_id": first_course["id"],
    }
