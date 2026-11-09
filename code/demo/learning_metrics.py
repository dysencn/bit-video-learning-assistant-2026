"""Shared, submission-based profiles for teacher and student views."""
from collections import Counter


def accepts_student(course, student):
    return student.get("major_id") in course.get("major_ids", [])


def build_profiles(courses, submissions, students):
    """All four axes share a course scope; no marks are invented for empty data."""
    course_by_id = {course["id"]: course for course in courses}
    records = [record for record in submissions if record["course_id"] in course_by_id]
    published = [course for course in courses if course["status"] == "published"]
    participants = {record["student_id"] for record in records}
    roster = [student for student in students if student["id"] in participants
              or any(accepts_student(course, student) for course in published)]
    profiles = []
    for student in roster:
        items = [record for record in records if record["student_id"] == student["id"]]
        weak = Counter()
        correct = total = 0
        for record in items:
            course = course_by_id[record["course_id"]]
            questions = {question["id"]: question for question in course.get("questions", [])}
            points = {point["id"]: point["name"] for point in course.get("knowledge_points", [])}
            for detail in record.get("details", []):
                total += 1
                correct += int(detail["correct"])
                if not detail["correct"]:
                    point = detail.get("knowledge_point_name") or points.get(
                        questions.get(detail["question_id"], {}).get("knowledge_point_id"), "未分类")
                    weak[point] += 1
        average = round(sum(item["score"] for item in items) / len(items)) if items else None
        eligible = {course["id"] for course in published if accepts_student(course, student)}
        attempted = {record["course_id"] for record in items}
        profiles.append({
            "student_id": student["id"], "student": student["name"], "major_id": student.get("major_id"),
            "submission_count": len(items), "course_count": len(attempted),
            "available_course_count": len(eligible), "average_score": average,
            "latest_submitted_at": max((item["submitted_at"] for item in items), default=None),
            "weak_points": [name for name, _ in weak.most_common(2)],
            "course_participation": round(len(attempted & eligible) / len(eligible) * 100) if eligible else 0,
            "mastery_score": round(correct / total * 100) if total else 0,
            "needs_attention": average is not None and average < 60, "not_started": not items,
        })
    maximum = max((profile["submission_count"] for profile in profiles), default=0)
    for profile in profiles:
        profile["practice_activity"] = round(profile["submission_count"] / maximum * 100) if maximum else 0
    return sorted(profiles, key=lambda p: (not p["not_started"], not p["needs_attention"],
                                           p["average_score"] if p["average_score"] is not None else 101,
                                           p["student_id"]))


def teacher_analytics(courses, submissions, students):
    course_ids = {course["id"] for course in courses}
    records = [record for record in submissions if record["course_id"] in course_ids]
    profiles = build_profiles(courses, records, students)
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
