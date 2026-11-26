"""Shared learning profiles for teacher and student views."""
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .enrollments import is_enrolled as accepts_student

READING_THRESHOLD = 60


def scoped_reading_progress(progress, courses):
    resources = {resource["id"]: (course["id"], resource.get("name"))
                 for course in courses for resource in course.get("resources", [])
                 if (resource.get("content") or "").strip()}
    entries = {key: value for key, value in progress.items() if key in resources
               and value.get("course_id", resources[key][0]) == resources[key][0]}
    return resources, entries


def _timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _rate(numerator, denominator):
    return {"value": round(numerator * 100 / denominator) if denominator else None,
            "numerator": numerator, "denominator": denominator}


def _average(scores):
    return {"value": round(sum(scores) / len(scores)) if scores else None, "denominator": len(scores)}


def _is_practice(record):
    return not record.get("assignment_id") and record.get("type") != "ai_quiz"


def _assignment_axes(items, eligible, assignments, now):
    attempts = defaultdict(list)
    for record in items:
        if record.get("assignment_id"):
            attempts[(record["course_id"], record["assignment_id"])].append(record)

    scores = []
    on_time = known_timing = 0
    earliest_possible = datetime.min.replace(tzinfo=timezone.utc)
    for key, records in attempts.items():
        ordered = sorted(records, key=lambda record: _timestamp(record.get("submitted_at")) or earliest_possible,
                         reverse=True)
        latest, first = ordered[0], ordered[-1]
        if type(latest.get("score")) in (int, float):
            scores.append(latest["score"])
        late = first.get("late")
        if type(late) is not bool:
            submitted = _timestamp(first.get("submitted_at"))
            due = assignments.get(key, {}).get("due")
            late = submitted > due if submitted is not None and due is not None else None
        if late is not None:
            known_timing += 1
            on_time += int(not late)

    missing = pending = 0
    for key, assignment in assignments.items():
        if key[0] not in eligible or key in attempts or assignment["status"] not in {"published", "closed"}:
            continue
        if assignment["opens"] is not None and now < assignment["opens"]:
            continue
        if assignment["status"] == "closed" or (assignment["due"] is not None and now >= assignment["due"]):
            missing += 1
        else:
            pending += 1
    return {
        "assignment_completion": {**_rate(len(attempts), len(attempts) + missing), "pending_count": pending},
        "assignment_on_time": _rate(on_time, known_timing),
        "assignment_average": _average(scores),
    }


def _radar_dimensions(items, eligible, practice_maximum, assignments, progress, review_counts, now):
    practice = [record for record in items if _is_practice(record)]
    judged = [detail["correct"] for record in practice for detail in record.get("details") or []
              if type(detail.get("correct")) is bool]
    practiced_or_submitted = {record["course_id"] for record in items
                              if _is_practice(record) or record.get("assignment_id")}
    history = eligible.keys() | practiced_or_submitted
    checked = [counts for course_id, counts in review_counts.items() if course_id in history]
    reviewed = sum(total for total, _ in checked)
    correct_reviews = sum(correct for _, correct in checked)
    resources, entries = scoped_reading_progress(progress, eligible.values())
    read = sum(int(entry.get("percent") or 0) >= READING_THRESHOLD for entry in entries.values())
    return {
        "course_participation": _rate(len(practiced_or_submitted & eligible.keys()), len(eligible)),
        "practice_activity": _rate(len(practice), practice_maximum),
        "practice_average": _average([record["score"] for record in practice if type(record.get("score")) in (int, float)]),
        "practice_accuracy": _rate(sum(judged), len(judged)),
        **_assignment_axes(items, eligible, assignments, now),
        "reading_completion": _rate(read, len(resources)),
        "review_accuracy": _rate(correct_reviews, reviewed),
    }


def build_profiles(courses, submissions, students, enrollments, *, radar_data=None,
                   include_student_id=None, now=None):
    """Keep summary statistics and calculate the radar from its separate sources."""
    course_by_id = {course["id"]: course for course in courses}
    records = [record for record in submissions if record["course_id"] in course_by_id]
    by_student = defaultdict(list)
    for record in records:
        by_student[record["student_id"]].append(record)
    published = [course for course in courses if course["status"] == "published"]
    roster = [student for student in students if student["id"] in by_student
              or student["id"] == include_student_id
              or any(accepts_student(course, student, enrollments) for course in published)]
    if radar_data is not None:
        current_time = now or datetime.now(timezone.utc)
        practice_counts = Counter(record["student_id"] for record in records if _is_practice(record))
        practice_maximum = max(practice_counts.values(), default=0)
        assignments = {(item["course_id"], item["id"]): {
            "status": item.get("status"), "opens": _timestamp(item.get("open_at")), "due": _timestamp(item.get("due_at")),
        } for item in radar_data.get("assignments", []) if item.get("course_id") in course_by_id}
        reading = radar_data.get("reading_progress") or {}
        reviews = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for event in radar_data.get("learning_events", []):
            if (event.get("type") == "review" and event.get("course_id") in course_by_id
                    and type(event.get("correct")) is bool):
                counts = reviews[event["student_id"]][event["course_id"]]
                counts[0] += 1
                counts[1] += int(event["correct"])
    profiles = []
    for student in roster:
        student_id = student["id"]
        items = by_student[student_id]
        weak = Counter()
        correct = total = 0
        for record in items:
            course = course_by_id[record["course_id"]]
            questions = {question["id"]: question for question in course.get("questions", [])}
            points = {point["id"]: point["name"] for point in course.get("knowledge_points", [])}
            for detail in record.get("details") or []:
                if type(detail.get("correct")) is not bool:
                    continue
                total += 1
                correct += int(detail["correct"])
                if not detail["correct"]:
                    point = detail.get("knowledge_point_name") or points.get(
                        questions.get(detail["question_id"], {}).get("knowledge_point_id"), "未分类")
                    weak[point] += 1
        average = round(sum(item["score"] for item in items) / len(items)) if items else None
        eligible = {course["id"]: course for course in published if accepts_student(course, student, enrollments)}
        attempted = {record["course_id"] for record in items}
        ordered = sorted(items, key=lambda item: item.get("submitted_at", ""))
        recent = ordered[-3:]
        previous = ordered[-6:-3]
        profile = {
            "student_id": student["id"], "student": student["name"], "major_id": student.get("major_id"),
            "submission_count": len(items), "course_count": len(attempted),
            "active_days": len({item.get("submitted_at", "")[:10] for item in items if item.get("submitted_at")}),
            "pass_rate": round(sum(item["score"] >= 60 for item in items) / len(items) * 100) if items else None,
            "recent_average": round(sum(item["score"] for item in recent) / len(recent)) if recent else None,
            "score_change": round(sum(item["score"] for item in recent) / len(recent) - sum(item["score"] for item in previous) / len(previous)) if len(recent) == 3 and len(previous) == 3 else None,
            "answered_count": total,
            "available_course_count": len(eligible), "average_score": average,
            "latest_submitted_at": max((item["submitted_at"] for item in items), default=None),
            "weak_points": [name for name, _ in weak.most_common(2)],
            "course_participation": round(len(attempted & eligible.keys()) / len(eligible) * 100) if eligible else 0,
            "mastery_score": round(correct / total * 100) if total else 0,
            "needs_attention": average is not None and average < 60, "not_started": not items,
        }
        if radar_data is not None and (include_student_id is None or student_id == include_student_id):
            profile["radar_dimensions"] = _radar_dimensions(
                items, eligible, practice_maximum, assignments, reading.get(student_id, {}),
                reviews[student_id], current_time)
        profiles.append(profile)
    maximum = max((profile["submission_count"] for profile in profiles), default=0)
    for profile in profiles:
        profile["practice_activity"] = round(profile["submission_count"] / maximum * 100) if maximum else 0
    return sorted(profiles, key=lambda p: (not p["not_started"], not p["needs_attention"],
                                           p["average_score"] if p["average_score"] is not None else 101,
                                           p["student_id"]))


def teacher_analytics(courses, submissions, students, enrollments, *, radar_data=None):
    course_ids = {course["id"] for course in courses}
    member_pairs = {(row["student_id"], row["course_id"]) for row in enrollments
                    if row.get("status", "active") == "active"}
    records = [record for record in submissions if record["course_id"] in course_ids
               and (record["student_id"], record["course_id"]) in member_pairs]
    profiles = build_profiles(courses, records, students, enrollments, radar_data=radar_data)
    stats = []
    for course in courses:
        items = [record for record in records if record["course_id"] == course["id"]]
        question_stats = []
        points = {point["id"]: point["name"] for point in course.get("knowledge_points", [])}
        for question in course.get("questions", []) + course.get("programming_questions", []):
            answers = [detail for record in items for detail in record.get("details", [])
                       if detail["question_id"] == question["id"]]
            if answers:
                question_stats.append({
                    "question_id": question["id"], "text": question["text"],
                    "knowledge_point": "Python 编程" if question.get("type") == "python" else points.get(question.get("knowledge_point_id"), "未分类"),
                    "answer_count": len(answers),
                    "correct_rate": round(sum(bool(detail["correct"]) for detail in answers) / len(answers) * 100),
                })
        stats.append({
            "course_id": course["id"], "title": course["title"], "status": course["status"],
            "teaching_class": course.get("teaching_class", ""), "course_code": course.get("course_code", ""),
            "major_ids": course["major_ids"], "participants": len({item["student_id"] for item in items}),
            "submission_count": len(items),
            "average_score": round(sum(item["score"] for item in items) / len(items)) if items else None,
            "pass_rate": round(sum(item["score"] >= 60 for item in items) / len(items) * 100) if items else None,
            "weak_questions": sorted(question_stats, key=lambda item: item["correct_rate"])[:3],
        })
    scores = [item["score"] for item in records]
    return {"overview": {
        "course_count": len(courses), "published_count": sum(c["status"] == "published" for c in courses),
        "student_count": len(profiles), "participant_count": len({item["student_id"] for item in records}),
        "submission_count": len(records), "average_score": round(sum(scores) / len(scores)) if scores else None,
        "pass_rate": round(sum(score >= 60 for score in scores) / len(scores) * 100) if scores else None,
        "latest_submitted_at": max((item["submitted_at"] for item in records), default=None),
        "attention_count": sum(profile["needs_attention"] or profile["not_started"] for profile in profiles),
    }, "courses": stats, "students": profiles}
