"""Course knowledge relationships and evidence-backed practice statistics."""
from collections import defaultdict, deque

from .learning_metrics import _timestamp


MASTERY_THRESHOLD = 80
MASTERY_PRIORITY = {"needs_review": 0, "unassessed": 1, "in_progress": 2}


def student_mastery(course, data, student_id):
    """Summarize the latest marked result per active bank question for this class."""
    course_id = course["id"]
    questions = {q["id"]: q["knowledge_point_id"] for q in course.get("questions", [])
                 if q.get("knowledge_point_id") and q.get("status") != "retired"}
    latest = {}

    def update(question_id, correct, stamp):
        if question_id not in questions or type(correct) is not bool:
            return
        moment = _timestamp(stamp)
        if moment is not None and (question_id not in latest or moment >= latest[question_id][0]):
            latest[question_id] = (moment, correct)

    for submission in data.get("submissions", []):
        if (submission.get("course_id") != course_id or submission.get("student_id") != student_id
                or submission.get("assignment_id") or submission.get("type") in {"ai_quiz", "python"}):
            continue
        for detail in submission.get("details") or []:
            update(detail.get("question_id"), detail.get("correct"), submission.get("submitted_at"))
    for wrong in data.get("wrong_questions", []):
        if (wrong.get("course_id") == course_id and wrong.get("student_id") == student_id
                and not wrong.get("assignment_id")):
            update(wrong.get("question_id"), wrong.get("last_review_result"), wrong.get("last_reviewed_at"))

    point_questions = defaultdict(list)
    for question_id, point_id in questions.items():
        point_questions[point_id].append(question_id)
    result = {}
    for point in course.get("knowledge_points", []):
        ids = point_questions[point["id"]]
        marks = [latest[question_id][1] for question_id in ids if question_id in latest]
        score = round(sum(marks) * 100 / len(marks)) if marks else None
        if score is None:
            status = "unassessed"
        elif score < MASTERY_THRESHOLD:
            status = "needs_review"
        elif len(marks) < len(ids):
            status = "in_progress"
        else:
            status = "mastered"
        result[point["id"]] = {"score": score, "status": status, "assessed": len(marks),
                               "correct": sum(marks), "question_count": len(ids)}
    return result


def mastery_plan_order(course, mastery, focus=None):
    """Put weak prerequisite nodes before their dependants, skipping established mastery."""
    prerequisites = defaultdict(list)
    for edge in (course.get("knowledge_graph") or {}).get("prerequisites", []):
        prerequisites[edge["after"]].append(edge["before"])
    candidates = [key for key, result in mastery.items() if result["status"] in MASTERY_PRIORITY]
    candidates.sort(key=lambda key: (MASTERY_PRIORITY[mastery[key]["status"]], mastery[key]["score"] or 0))
    pending, ordered = set(candidates), []

    def visit(point_id, prerequisite=False):
        if point_id not in pending:
            return
        pending.remove(point_id)
        for before in prerequisites[point_id]:
            visit(before, True)
        ordered.append((point_id, prerequisite))

    if focus is None:
        for point_id in candidates:
            visit(point_id)
    elif focus in pending:
        visit(focus)
    return ordered


def slide_page(job, page_number):
    if not job or job.get("status") != "completed":
        return None
    return next((page for page in job.get("result", {}).get("pages", [])
                 if page.get("number") == page_number), None)


def build_graph_update(course, payload, get_job):
    edges, sources = payload.get("prerequisites"), payload.get("evidence")
    if not isinstance(edges, list) or not isinstance(sources, list):
        raise ValueError("请提交先修关系和材料关联列表")
    points = {point["id"] for point in course.get("knowledge_points", [])}
    outgoing = defaultdict(list)
    degrees = dict.fromkeys(points, 0)
    seen_edges, prerequisites = set(), []
    for edge in edges:
        before = edge.get("before") if isinstance(edge, dict) else None
        after = edge.get("after") if isinstance(edge, dict) else None
        if not isinstance(before, str) or not isinstance(after, str) or before not in points or after not in points:
            raise ValueError("先修关系的两端必须是本班知识点")
        if before == after or (before, after) in seen_edges:
            raise ValueError("先修关系不能指向自身或重复添加")
        seen_edges.add((before, after))
        prerequisites.append({"before": before, "after": after})
        outgoing[before].append(after)
        degrees[after] += 1
    queue = deque(point for point, degree in degrees.items() if degree == 0)
    visited = 0
    while queue:
        visited += 1
        for after in outgoing[queue.popleft()]:
            degrees[after] -= 1
            if degrees[after] == 0:
                queue.append(after)
    if visited != len(points):
        raise ValueError("先修关系形成循环，请检查学习顺序")

    resources = {resource["id"]: resource for resource in course.get("resources", [])}
    previous = {(item["knowledge_point_id"], item["resource_id"], item["page"]): item
                for item in course.get("knowledge_graph", {}).get("evidence", [])}
    seen_sources, evidence = set(), []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("材料关联格式错误")
        point_id, resource_id, page = source.get("knowledge_point_id"), source.get("resource_id"), source.get("page")
        if not isinstance(point_id, str) or point_id not in points or not isinstance(resource_id, str):
            raise ValueError("材料只能关联本班知识点与资料")
        if page is not None and (type(page) is not int or page < 1):
            raise ValueError("课件页码必须是正整数")
        key = (point_id, resource_id, page)
        if key in seen_sources:
            raise ValueError("同一知识点下的资料或课件页不可重复关联")
        seen_sources.add(key)
        resource = resources.get(resource_id)
        job_id = resource.get("job_id") if resource else None
        slide = slide_page(get_job(job_id), page) if job_id and page is not None else None
        if not resource or (page is not None and not slide):
            if key in previous:
                evidence.append(previous[key])
                continue
            raise ValueError("资料或课件页不存在，或不属于本班")
        evidence.append({"knowledge_point_id": point_id, "resource_id": resource_id, "page": page,
                         "title": resource["name"], "job_id": job_id,
                         "timestamp": slide.get("timestamp") if slide else None})
    return {"prerequisites": prerequisites, "evidence": evidence}


def graph_view(course, data, student_ids, *, teacher, get_job):
    graph = course.get("knowledge_graph", {})
    course_id = course["id"]
    nodes = {point["id"]: {**point, "evidence": [], "practice_chapters": [], "answered": 0,
                           "correct": 0, "review_count": 0, "review_correct": 0}
             for point in course.get("knowledge_points", [])}
    resources = {resource["id"]: resource for resource in course.get("resources", [])}
    for source in graph.get("evidence", []):
        node = nodes.get(source["knowledge_point_id"])
        if node is None:
            continue
        resource = resources.get(source["resource_id"])
        job_id = resource.get("job_id") if resource else None
        page = source["page"]
        available = bool(resource and job_id == source.get("job_id"))
        if available and job_id:
            job = get_job(job_id)
            available = bool(slide_page(job, page) if page is not None else job and job.get("status") == "completed")
        node["evidence"].append({
            "resource_id": source["resource_id"], "name": resource["name"] if resource else source["title"],
            "page": page, "timestamp": source["timestamp"], "available": available,
            "job_id": job_id if available else None,
        })

    chapters = {chapter["id"]: chapter["title"] for chapter in course.get("chapters", [])}
    point_chapters = defaultdict(set)
    for question in course.get("questions", []):
        point_id = question.get("knowledge_point_id")
        if isinstance(point_id, str) and question.get("status") != "retired":
            point_chapters[point_id].add(question.get("chapter_id"))
    for point_id, node in nodes.items():
        node["practice_chapters"] = [{"id": key, "title": title} for key, title in chapters.items()
                                     if key in point_chapters[point_id]]

    for submission in data.get("submissions", []):
        if (submission.get("course_id") != course_id or submission.get("student_id") not in student_ids
                or submission.get("assignment_id") or submission.get("type") == "python"):
            continue
        for detail in submission.get("details", []):
            node = nodes.get(detail.get("knowledge_point_id"))
            if node is not None:
                node["answered"] += 1
                node["correct"] += int(detail.get("correct") is True)
    for event in data.get("learning_events", []):
        if (event.get("course_id") != course_id or event.get("student_id") not in student_ids
                or event.get("type") != "review"):
            continue
        node = nodes.get(event.get("knowledge_point_id"))
        if node is not None:
            node["review_count"] += 1
            node["review_correct"] += int(event.get("correct") is True)

    pending = defaultdict(list)
    mastery = {}
    if not teacher:
        mastery = student_mastery(course, data, next(iter(student_ids)))
        for wrong in data.get("wrong_questions", []):
            if (wrong.get("course_id") == course_id and wrong.get("student_id") in student_ids
                    and not wrong.get("assignment_id") and wrong.get("status") != "mastered"
                    and wrong.get("next_review_at")):
                pending[wrong.get("knowledge_point_id")].append(wrong["next_review_at"])
    for point_id, node in nodes.items():
        node["accuracy"] = round(node["correct"] / node["answered"] * 100) if node["answered"] else None
        if not teacher:
            node["mastery"] = mastery[point_id]
            node["pending_review_count"] = len(pending[point_id])
            node["next_review_at"] = min(pending[point_id], default=None)
    return {"course_id": course_id, "title": course["title"],
            "teaching_class": course.get("teaching_class", ""),
            "scope": "class" if teacher else "student",
            "editable": teacher and course.get("status") != "archived",
            "revision": graph.get("revision", 0), "nodes": list(nodes.values()),
            **({"mastery_threshold": MASTERY_THRESHOLD} if not teacher else {}),
            "prerequisites": graph.get("prerequisites", [])}
