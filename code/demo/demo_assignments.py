"""Make labelled sample grades traceable to published, timed assignment attempts."""
from datetime import datetime, timedelta, timezone
import re
import hashlib


def upgrade_assignment_titles(data):
    """Remove the old suffix from generated assignment titles, preserving custom titles."""
    if data.get("demo_assignment_titles_version", 0) >= 1:
        return False
    for assignment in data.get("assignments", []):
        if assignment.get("demo") and assignment.get("id", "").startswith("demo-assignment-"):
            assignment["title"] = re.sub(r"( · (?:课后作业|历史作业))（演示）$", r"\1", assignment.get("title", ""))
    data["demo_assignment_titles_version"] = 1
    return True


def prepare_demo_assignments(data, accounts):
    courses = {course["id"]: course for course in data["courses"]}
    students = {student["id"]: student for student in accounts if student.get("demo")}
    python_classes = {course["major_ids"][0]: course for course in courses.values()
                      if course.get("demo") and course.get("course_code") == "PY101"
                      and len(course.get("major_ids", [])) == 1 and course.get("status") == "published"}

    def moved(item, details):
        if item.get("assignment_id") and not item["assignment_id"].startswith("demo-assignment-"):
            return
        source = courses.get(item.get("course_id"))
        student = students.get(item.get("student_id"))
        if not source or not student or source.get("course_code") != "PY101":
            return
        target = python_classes.get(student.get("major_id"))
        if not target or target["id"] == source["id"]:
            return
        maps = {}
        for field in ("questions", "chapters", "knowledge_points"):
            ids = {obj["id"] for obj in target.get(field, [])}
            maps[field] = {obj["id"]: next((value for value in ids if value == obj["id"]
                or value == f"{target['id']}-{obj['id']}"), obj["id"]) for obj in source.get(field, [])}
        # Only generated sample histories move; real submissions always keep their original evidence.
        item["course_id"] = target["id"]
        for key, field in (("question_id", "questions"), ("chapter_id", "chapters"), ("knowledge_point_id", "knowledge_points")):
            if item.get(key): item[key] = maps[field].get(item[key], item[key])
        for detail in details:
            for key, field in (("question_id", "questions"), ("chapter_id", "chapters")):
                if detail.get(key): detail[key] = maps[field].get(detail[key], detail[key])

    for record in data["submissions"]:
        if record.get("demo") and re.fullmatch(r"demo-sub-\d+-.*-\d+", record.get("id", "")):
            moved(record, record.get("details", []))
    for wrong in data["wrong_questions"]:
        if wrong.get("demo") and wrong.get("id", "").startswith("demo-wrong-"):
            moved(wrong, [])

    now = datetime.now(timezone.utc)
    assignments = data.setdefault("assignments", [])
    attempts = data.setdefault("assignment_attempts", [])
    by_id = {item["id"]: item for item in assignments}
    attempt_ids = {item["id"] for item in attempts}
    for course in courses.values():
        if not course.get("demo") or course.get("status") != "published":
            continue
        for chapter in course.get("chapters", []):
            records = [record for record in data["submissions"] if record.get("demo")
                       and record.get("id", "").startswith("demo-sub-")
                       and record["course_id"] == course["id"] and record.get("chapter_id") == chapter["id"]]
            active = {q["id"]:q for q in course["questions"] if q.get("chapter_id") == chapter["id"] and q.get("status") != "retired"}
            groups = {tuple(active): []}
            for record in records:
                selected = {detail["question_id"] for detail in record.get("details", [])}
                if selected and selected.issubset(active):
                    ids = tuple(qid for qid in active if qid in selected)
                    groups.setdefault(ids, []).append(record)
            for question_ids, records in groups.items():
                if not question_ids:
                    continue
                historic = set(question_ids) != set(active)
                suffix = "-" + hashlib.sha256("|".join(question_ids).encode()).hexdigest()[:8] if historic else ""
                assignment_id = f"demo-assignment-{course['id']}-{chapter['id']}{suffix}"
                earliest = min((datetime.fromisoformat(r["submitted_at"]) for r in records), default=now-timedelta(days=6))
                opened = min(now-timedelta(days=8), earliest-timedelta(hours=1))
                created = opened-timedelta(days=1)
                if assignment_id not in by_id:
                    item = {"id":assignment_id, "course_id":course["id"], "teacher_id":course["teacher_id"],
                        "title":f"{chapter['title']} · {'历史作业' if historic else '课后作业'}", "description":"按章节完成单选题，提交后查看得分与解析。",
                        "chapter_id":chapter["id"], "question_ids":list(question_ids),
                        "status":"published", "open_at":opened.isoformat(), "due_at":(now+timedelta(days=7)).isoformat(),
                        "close_at":None, "max_attempts":3, "time_limit_minutes":0, "allow_late":False,
                        "late_max_days":0, "late_penalty_percent_per_day":0, "grade_release":"immediate",
                        "grades_released_at":None, "created_at":created.isoformat(), "updated_at":created.isoformat(), "demo":True}
                    assignments.append(item); by_id[assignment_id] = item
                    if course.get("created_at", created.isoformat()) > created.isoformat():
                        course["created_at"] = (created-timedelta(days=1)).isoformat()
                for record in records:
                    if record.get("assignment_id"):
                        continue
                    if set(detail["question_id"] for detail in record.get("details", [])) != set(question_ids):
                        continue
                    attempt_id = f"demo-attempt-{record['id']}"
                    stamp = datetime.fromisoformat(record["submitted_at"])
                    if attempt_id not in attempt_ids:
                        attempts.append({"id":attempt_id, "assignment_id":assignment_id, "course_id":course["id"],
                            "student_id":record["student_id"], "started_at":(stamp-timedelta(minutes=5)).isoformat(),
                            "status":"submitted", "submitted_at":record["submitted_at"], "submission_id":record["id"], "demo":True})
                        attempt_ids.add(attempt_id)
                    record.update(assignment_id=assignment_id, attempt_id=attempt_id, raw_score=record["score"],
                                  late=False, late_days=0, penalty_percent=0, duration_seconds=300)
    data["demo_assignment_version"] = 1
