def pick_next_best_action(jobs, applications):
    open_jobs = [j for j in jobs if j["status"] == "open"]
    applied_job_ids = {a["job_posting_id"] for a in applications}

    unattended = [j for j in open_jobs if j["id"] not in applied_job_ids]
    if unattended:
        job = unattended[0]
        return {
            "title": f"\"{job['title']}\" has no applicants yet",
            "detail": "Widen the required skills or share the posting to reach more candidates.",
        }

    pending = [a for a in applications if a["status"] == "submitted"]
    if pending:
        return {
            "title": f"{len(pending)} application(s) awaiting review",
            "detail": "Shortlist or respond so candidates stay engaged.",
        }

    return None


def compute_conversion_rate(applications):
    if not applications:
        return 0
    hired = sum(1 for a in applications if a["status"] == "hired")
    return round((hired / len(applications)) * 100)
