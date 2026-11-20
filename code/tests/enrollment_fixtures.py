"""Existing feature fixtures explicitly assign students before exercising their feature."""
import json
from urllib.request import Request


def enroll_matching_students(test, course_id, teacher="teacher-demo-1"):
    url = test.url + f"/api/courses/{course_id}/enrollments"
    headers = {"X-Local-Token": test.token, "X-Account-ID": teacher, "Content-Type": "application/json"}
    with test.client.open(Request(url, headers=headers), timeout=5) as response:
        candidates = json.load(response)["students"]
    student_ids = [student["id"] for student in candidates if student["enrolled"] or not student["other_classes"]]
    with test.client.open(Request(url, data=json.dumps({"student_ids": student_ids}).encode(),
                                  headers=headers, method="PUT"), timeout=5) as response:
        return json.load(response)
