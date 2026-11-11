"""Assignment-owned questions and per-student answer disclosure."""
from copy import deepcopy
import uuid


def answers_visible(assignment, student_id, submissions):
    if not assignment:
        return False
    count = sum(record.get("assignment_id") == assignment["id"]
                and record.get("student_id") == student_id for record in submissions)
    return count >= max(1, int(assignment.get("max_attempts") or 1))


def record_answers_visible(record, assignments, submissions):
    if not record.get("assignment_id"):
        return True
    return answers_visible(assignments.get(record["assignment_id"]), record.get("student_id"), submissions)


def upgrade_independent_assignments(data):
    """Snapshot legacy papers without changing recorded answers, scores or attempt counts."""
    changed = data.get("independent_assignments_version") != 1
    courses = {course["id"]: course for course in data.get("courses", [])}
    for assignment in data.get("assignments", []):
        if "questions" in assignment:
            continue
        course = courses.get(assignment.get("course_id"), {})
        ids = assignment.get("question_ids") or []
        source = [q for q in course.get("questions", []) if (q["id"] in ids if ids else
                  q.get("chapter_id") == assignment.get("chapter_id"))]
        records = [r for r in data.get("submissions", []) if r.get("assignment_id") == assignment["id"]]
        by_id = {q["id"]: deepcopy(q) for q in source}
        # Recorded snapshots survive course edits and retired/deleted bank questions.
        for record in reversed(records):
            for detail in record.get("details", []):
                by_id[detail["question_id"]] = {**by_id.get(detail["question_id"], {}),
                    **{key: deepcopy(detail[key]) for key in ("text", "options", "answer", "explanation", "chapter_id") if key in detail},
                    "id": detail["question_id"]}
        mapping = {qid: f"aq-{assignment['id']}-{qid}" for qid in by_id}
        assignment["questions"] = [{"id": mapping[qid], "text": q.get("text", "历史题目"),
            "options": deepcopy(q.get("options", [])), "answer": q.get("answer", 0),
            "explanation": q.get("explanation", ""), "chapter_id": q.get("chapter_id") or assignment.get("chapter_id")}
            for qid, q in by_id.items()]
        assignment.pop("question_ids", None)
        assignment["grade_release"] = "immediate"
        for record in records:
            for detail in record.get("details", []):
                detail["question_id"] = mapping.get(detail["question_id"], detail["question_id"])
        for wrong in data.get("wrong_questions", []):
            if wrong.get("demo") and not wrong.get("assignment_id") and wrong.get("question_id") in mapping:
                if any(record.get("demo") and record.get("student_id") == wrong.get("student_id")
                       and any(detail.get("question_id") == mapping[wrong["question_id"]] and not detail.get("correct")
                               for detail in record.get("details", [])) for record in records):
                    wrong["assignment_id"] = assignment["id"]
            if wrong.get("assignment_id") == assignment["id"]:
                wrong["question_id"] = mapping.get(wrong["question_id"], wrong["question_id"])
        changed = True
    data["independent_assignments_version"] = 1
    return changed


def validate_questions(raw, course, previous=()):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ValueError("请先为本次作业创建 1–100 道独立题目")
    old = {q["id"]: q for q in previous}
    chapter_ids = {chapter["id"] for chapter in course["chapters"]}
    result, used = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("作业题目格式不正确")
        qid = item.get("id")
        if qid is not None and (not isinstance(qid, str) or qid not in old or qid in used):
            raise ValueError("只能编辑本次作业的题目，不能引用课程题库或其他作业的题目")
        qid = qid or "aq-" + uuid.uuid4().hex[:16]
        used.add(qid)
        text, options, answer = item.get("text"), item.get("options"), item.get("answer")
        explanation = item.get("explanation", "")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 3000:
            raise ValueError("请填写 1–3000 字的作业题干")
        if not isinstance(options, list) or not 2 <= len(options) <= 4 or any(
                not isinstance(option, str) or not option.strip() or len(option.strip()) > 1000 for option in options):
            raise ValueError("请填写 2–4 个有效选项，每项最多 1000 字")
        if type(answer) is not int or not 0 <= answer < len(options):
            raise ValueError("请选择正确答案")
        if not isinstance(explanation, str) or len(explanation.strip()) > 3000:
            raise ValueError("解析最多 3000 字")
        chapter_id = item.get("chapter_id") or course["chapters"][0]["id"]
        if chapter_id not in chapter_ids:
            raise ValueError("作业题目所属章节不存在")
        result.append({"id": qid, "text": text.strip(), "options": [option.strip() for option in options],
                       "answer": answer, "explanation": explanation.strip(), "chapter_id": chapter_id})
    return result
