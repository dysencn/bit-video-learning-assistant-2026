"""Explicit student–teaching-class membership; major matching alone grants no access."""
from datetime import datetime, timezone
import uuid

from fastapi import HTTPException


def matches_major(course, student):
    return student.get("role") == "student" and student.get("major_id") in course.get("major_ids", [])


def is_enrolled(course, student, enrollments):
    return matches_major(course, student) and any(
        item.get("course_id") == course["id"] and item.get("student_id") == student["id"]
        and item.get("status", "active") == "active" for item in enrollments)


def subject_key(course):
    code = str(course.get("course_code") or "").strip().upper()
    return (code, str(course.get("term") or "").strip()) if code else (course["id"], "")


def upgrade_enrollments(data, students):
    """One-time migration: retain historical access, then assign one class per subject/term."""
    if data.get("enrollments_version") == 1:
        return False
    now = datetime.now(timezone.utc).isoformat()
    rows = data.setdefault("enrollments", [])
    existing = {(row.get("student_id"), row.get("course_id")) for row in rows}
    courses = data["courses"]
    # The existing second AI demo class now demonstrates a separate, explicit roster.
    second = next((c for c in courses if c["id"] == "course-deep-learning-02"), None)
    if second and second.get("demo") and second.get("status") == "draft" \
            and second.get("teaching_class") == "人工智能 02班" and second.get("teacher_id") == "teacher-demo-2":
        second["status"] = "published"
    for student in students:
        groups = {}
        for course in courses:
            if course.get("status") == "published" and matches_major(course, student):
                groups.setdefault(subject_key(course), []).append(course)
        history = {r.get("course_id") for r in data.get("submissions", []) if r.get("student_id") == student["id"]}
        for choices in groups.values():
            selected = [c for c in choices if c["id"] in history]
            if not selected:
                preferred = "course-deep-learning-02" if student["id"] == "student-demo-7" else ""
                selected = [next((c for c in choices if c["id"] == preferred), choices[0])]
            for course in selected:
                if (student["id"], course["id"]) not in existing:
                    rows.append({"id": uuid.uuid4().hex[:12], "student_id": student["id"],
                                 "course_id": course["id"], "status": "active", "source": "migration",
                                 "created_at": now, "updated_at": now})
                    existing.add((student["id"], course["id"]))
    data["enrollments_version"] = 1
    return True


def replace_roster(data, course, students, student_ids):
    """Validate the complete roster before changing any membership (unique pair, no cross-major joins)."""
    if not isinstance(student_ids, list) or any(not isinstance(sid, str) for sid in student_ids):
        raise HTTPException(400, "学生名单格式无效")
    if len(set(student_ids)) != len(student_ids):
        raise HTTPException(400, "学生名单不能重复")
    by_id = {student["id"]: student for student in students}
    if any(sid not in by_id or not matches_major(course, by_id[sid]) for sid in student_ids):
        raise HTTPException(400, "只能添加本教学班专业的学生")
    rows = data.setdefault("enrollments", [])
    courses = {c["id"]: c for c in data["courses"]}
    old_ids = {row["student_id"] for row in rows if row.get("course_id") == course["id"]
               and row.get("status", "active") == "active"}
    # Adding someone must not silently move them out of another teacher's class.
    for row in rows:
        other = courses.get(row.get("course_id"))
        sid = row.get("student_id")
        if sid in set(student_ids) - old_ids and row.get("status", "active") == "active" \
                and other and other["id"] != course["id"] and other.get("status") != "archived" \
                and subject_key(other) == subject_key(course):
            raise HTTPException(409, f"{by_id[sid]['name']}已在同课程同学期的「{other.get('teaching_class') or other['title']}」，请先从原班移出")
    now = datetime.now(timezone.utc).isoformat()
    wanted = set(student_ids)
    known = set()
    for row in rows:
        if row.get("course_id") != course["id"]:
            continue
        known.add(row["student_id"])
        desired = "active" if row["student_id"] in wanted else "removed"
        if row.get("status", "active") != desired:
            row.update(status=desired, updated_at=now)
    for sid in student_ids:
        if sid not in known:
            rows.append({"id": uuid.uuid4().hex[:12], "student_id": sid, "course_id": course["id"],
                         "status": "active", "source": "teacher", "created_at": now, "updated_at": now})
