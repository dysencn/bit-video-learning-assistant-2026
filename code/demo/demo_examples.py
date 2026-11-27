"""Versioned examples for the built-in classes and demo students."""
from datetime import datetime, timedelta, timezone

from .demo_assignments import upgrade_assignment_examples
from .enrollments import is_enrolled
from .knowledge_graph import build_graph_update
from .learning_metrics import READING_THRESHOLD
from .teaching_catalog import teaching_samples


def _seed_graph(course, sample):
    if "knowledge_graph" in course:
        return
    names = [point["id"] for point in sample["knowledge_points"]]
    valid = {point["id"] for point in course["knowledge_points"]}
    edges = [(0, 1), (1, 2)] if sample["course_code"] == "WEB101" else [
        (0, 1), (0, 2), (2, 3), (3, 4), (1, 5),
    ]
    prerequisites = [{"before": names[before], "after": names[after]} for before, after in edges
                     if names[before] in valid and names[after] in valid]
    expected_resources = {resource["id"] for resource in sample["resources"]}
    sample_points = valid.intersection(names)
    links = {(q["chapter_id"], q["knowledge_point_id"]) for q in course["questions"]
             if q.get("chapter_id") and q.get("status") != "retired" and q.get("knowledge_point_id") in sample_points}
    evidence = [{"knowledge_point_id": point, "resource_id": resource["id"], "page": None}
                for resource in course["resources"] if resource["id"] in expected_resources and resource.get("content")
                for chapter, point in sorted(links) if chapter == resource.get("chapter_id")]
    if prerequisites or evidence:
        course["knowledge_graph"] = {**build_graph_update(course, {"prerequisites": prerequisites, "evidence": evidence},
                                                          get_job=lambda _: None), "revision": 1}


def _seed_practice(data, course, sample, student, now, record_event):
    student_id, course_id = student["id"], course["id"]
    if any(record.get("student_id") == student_id and record.get("course_id") == course_id
           and not record.get("demo") for key in ("submissions", "wrong_questions") for record in data[key]):
        return
    points = {point["id"]: point["name"] for point in course["knowledge_points"]}
    chapters = {chapter["id"]: chapter["title"] for chapter in course["chapters"]}
    reference = {question["id"]: question for question in sample["questions"]}
    bank = [question for question in course["questions"] if question["id"] in reference
            and question.get("status") != "retired" and question.get("chapter_id") in chapters
            and all(question.get(key) == reference[question["id"]].get(key)
                    for key in ("text", "options", "answer", "knowledge_point_id"))]
    wrongs = {record["question_id"] for record in data["wrong_questions"]
              if record.get("student_id") == student_id and record.get("course_id") == course_id}
    for index, original in enumerate(sample["knowledge_points"][:2]):
        point_id = original["id"]
        questions = [question for question in bank if question.get("knowledge_point_id") == point_id]
        if point_id not in points or not questions or any(q["id"] in wrongs for q in questions):
            continue
        passed = index == 0
        submission_id = f"demo-mastery-{student_id}-{course_id}-{index + 1}"
        submitted = now - timedelta(days=3 - index)
        stamp = submitted.isoformat()
        details = []
        for q_index, question in enumerate(questions):
            question_id, chapter_id = question["id"], question["chapter_id"]
            snapshot = {key: question[key] for key in ("text", "options", "answer", "explanation") if key in question}
            selected = question["answer"] if passed else (question["answer"] + 1) % len(question["options"])
            details.append({**snapshot, "question_id": question_id, "chapter_id": chapter_id,
                            "knowledge_point_id": point_id, "knowledge_point_name": points[point_id],
                            "selected": selected, "correct": passed})
            if passed:
                continue
            wrong_id = f"{submission_id}-{question_id}"
            wrong = {"id": wrong_id, "student_id": student_id, "course_id": course_id,
                     "question_id": question_id, "knowledge_point_id": point_id,
                     "knowledge_point_name": points[point_id], "chapter_id": chapter_id,
                     "chapter_title": chapters[chapter_id], "question_snapshot": snapshot,
                     "wrong_count": 1, "last_wrong_at": stamp, "first_wrong_at": stamp,
                     "status": "unresolved", "review_interval_days": 1, "review_count": 0,
                     "next_review_at": (submitted + timedelta(days=1)).isoformat(), "demo": True}
            if q_index == 0:
                reviewed = now - timedelta(days=1)
                correct = len(questions) > 1
                wrong.update(last_reviewed_at=reviewed.isoformat(), last_review_result=correct,
                             review_count=1, wrong_count=1 if correct else 2,
                             last_wrong_at=stamp if correct else reviewed.isoformat(),
                             review_interval_days=2 if correct else 1)
                wrong["next_review_at"] = (reviewed + timedelta(days=wrong["review_interval_days"])).isoformat()
                record_event(data, student_id, "review", reviewed, course_id=course_id, ref_id=wrong_id,
                             correct=correct, knowledge_point_id=point_id)["demo"] = True
            data["wrong_questions"].append(wrong)
        chapter_ids = {question["chapter_id"] for question in questions}
        data["submissions"].append({"id": submission_id, "course_id": course_id, "student_id": student_id,
                                    "student": student["name"], "submitted_at": stamp, "score": 100 if passed else 0,
                                    "correct_count": len(questions) if passed else 0, "total": len(questions),
                                    "chapter_id": next(iter(chapter_ids)) if len(chapter_ids) == 1 else None,
                                    "details": details, "demo": True})
        record_event(data, student_id, "practice_submit", submitted, course_id=course_id,
                     ref_id=submission_id)["demo"] = True

    progress = data.setdefault("reading_progress", {}).setdefault(student_id, {})
    resources = {resource["id"]: resource for resource in course["resources"] if (resource.get("content") or "").strip()}
    read_at = (now - timedelta(days=3, hours=1)).isoformat()
    for reference_resource, percent in zip(sample["resources"], (100, 35)):
        resource_id = reference_resource["id"]
        if resource_id not in resources or resource_id in progress:
            continue
        recorded = percent >= READING_THRESHOLD
        progress[resource_id] = {"course_id": course_id, "name": resources[resource_id]["name"],
                                 "percent": percent, "first_at": read_at, "updated_at": read_at,
                                 "recorded": recorded, "demo": True}
        if recorded:
            record_event(data, student_id, "resource_read", read_at, course_id=course_id, ref_id=resource_id)["demo"] = True


def upgrade_demo_examples(data, accounts, record_event):
    if data.get("demo_mastery_examples_version") == 1:
        return False
    now = datetime.now(timezone.utc)
    samples = teaching_samples()
    upgrade_assignment_examples(data, samples, now)
    courses = {course["id"]: course for course in data["courses"]
               if course.get("demo") and course.get("status") == "published"}
    students = [student for student in accounts if student.get("demo")
                and student["id"] in {"student-demo-1", "student-demo-2"}]
    for sample in samples:
        course = courses.get(sample["id"])
        if course is None:
            continue
        _seed_graph(course, sample)
        for student in students:
            if is_enrolled(course, student, data.get("enrollments", [])):
                _seed_practice(data, course, sample, student, now, record_event)
    data["submissions"].sort(key=lambda record: record.get("submitted_at") or "", reverse=True)
    data["demo_mastery_examples_version"] = 1
    return True
