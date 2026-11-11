"""An explicitly labelled demo catalogue; saved sample answers are graded like real ones."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from .programming import LIMITS as PYTHON_LIMITS

MAJORS = [
    {"id": "m-soft", "name": "软件工程"},
    {"id": "m-ai", "name": "人工智能"},
    {"id": "m-cs", "name": "计算机科学"},
]


def question(qid, chapter, point, text, options, answer, explanation):
    return {"id": qid, "chapter_id": chapter, "knowledge_point_id": point,
            "text": text, "options": options, "answer": answer, "explanation": explanation}


def demo_programming_questions():
    """Stable sample IDs, public examples and judge data; no reference solutions."""
    def build(key, chapter, title, description, examples, hidden):
        pairs = lambda rows: [{"input": source, "output": expected} for source, expected in rows]
        return {"id": f"py-demo-{key}", "type": "python", "demo": True,
                "chapter_id": chapter, "title": title, "text": title,
                "description": description + "\n\n请从标准输入读取数据，只输出答案，不要输出‘请输入’等提示文字。",
                "examples": pairs(examples), "test_cases": pairs(examples + hidden),
                "limits": dict(PYTHON_LIMITS)}

    return [
        build("sum", "ch-1", "两个整数之和",
              "读取两个整数 a 和 b，计算并输出它们的和。\n输入：一行两个整数，以空格分隔，-1000000 ≤ a, b ≤ 1000000。\n输出：一行，表示 a + b 的整数。",
              [("2 3\n", "5\n"), ("-8 1\n", "-7\n")],
              [("0 0\n", "0\n"), ("1000000 1000000\n", "2000000\n"), ("-1000000 1000000\n", "0\n")]),
        build("length", "ch-1", "字符串长度",
              "读取一行文字，统计字符个数。中文、字母和空格都各算一个字符，行末换行符不计入。\n输入：一行字符串，长度为 0～100，可能为空或包含首尾空格。\n输出：一行整数，表示字符个数。",
              [("Python\n", "6\n"), ("你好 Python\n", "9\n")],
              [("\n", "0\n"), (" a b \n", "5\n"), ("x" * 100 + "\n", "100\n")]),
        build("parity", "ch-2", "奇偶判断",
              "判断一个整数是偶数还是奇数。0 是偶数，负整数也需要正确处理。\n输入：一行整数 n，-1000000 ≤ n ≤ 1000000。\n输出：偶数输出 even，奇数输出 odd，均使用小写字母。",
              [("8\n", "even\n"), ("7\n", "odd\n")],
              [("0\n", "even\n"), ("-3\n", "odd\n"), ("-1000000\n", "even\n")]),
        build("accumulate", "ch-2", "从 1 累加到 n",
              "计算 1 + 2 + … + n。n 为 0 时结果为 0。可以用循环或求和公式实现。\n输入：一行整数 n，0 ≤ n ≤ 100000。\n输出：一行整数，表示累加结果。",
              [("5\n", "15\n"), ("0\n", "0\n")],
              [("1\n", "1\n"), ("100\n", "5050\n"), ("100000\n", "5000050000\n")]),
        build("maximum", "ch-3", "列表最大值",
              "读取一个整数列表，输出其中最大的数。注意所有元素都为负数的情况。\n输入：第一行是元素个数 n（1 ≤ n ≤ 100）；第二行是 n 个以空格分隔的整数，每个数的范围为 -1000000～1000000。\n输出：一行整数，表示最大值。",
              [("5\n3 8 2 8 -1\n", "8\n"), ("3\n-9 -2 -7\n", "-2\n")],
              [("1\n-1000000\n", "-1000000\n"), ("4\n0 0 0 0\n", "0\n"),
               ("100\n" + " ".join(str(n) for n in range(1, 101)) + "\n", "100\n")]),
        build("frequency", "ch-3", "单词频次统计",
              "统计目标单词在给定单词列表中出现的次数，区分大小写。可以用循环或字典统计。\n输入：第一行是 n（1 ≤ n ≤ 100）；第二行是 n 个用空格分隔的英文单词；第三行是目标单词。每个单词由 1～20 个英文字母组成。\n输出：一行整数，表示目标单词的出现次数。",
              [("5\npython java python cpp python\npython\n", "3\n"), ("3\nPython python PYTHON\npython\n", "1\n")],
              [("3\na b c\nz\n", "0\n"), ("1\nhello\nhello\n", "1\n"), ("4\nhi hi hi hi\nhi\n", "4\n")]),
    ]


def demo_courses():
    now = datetime.now(timezone.utc).isoformat()
    python = {
        "id": "course-demo", "title": "Python 程序设计入门", "teacher": "王老师",
        "teacher_id": "teacher-demo-1", "description": "从变量与流程控制到函数和容器，用可运行的代码建立编程思维。适合所有专业的第一门编程课。",
        "status": "published", "created_at": now, "major_ids": [m["id"] for m in MAJORS],
        "category": "python", "demo": True, "level": "零基础", "duration": "约 3 小时",
        "programming_questions": demo_programming_questions(),
        "chapters": [
            {"id": "ch-1", "title": "变量与数据类型", "order": 1},
            {"id": "ch-2", "title": "输出与程序结构", "order": 2},
            {"id": "ch-3", "title": "函数与数据容器", "order": 3},
        ],
        "knowledge_points": [{"id": key, "name": name} for key, name in [
            ("kp1", "变量与数据类型"), ("kp2", "字符串类型"), ("kp3", "print 输出函数"),
            ("kp4", "条件与循环"), ("kp5", "函数与返回值"), ("kp6", "列表与字典")]],
        "resources": [
            {"id": "res-demo", "name": "学习讲义：变量、类型与表达式", "kind": "图文讲义", "job_id": None,
             "chapter_id": "ch-1", "content": "变量把名字与对象关联，赋值使用等号，比较相等使用双等号。整数、浮点数、字符串和布尔值是常见类型。\n字符串使用引号包围，数字外观的字符串也不是整数。可以用 type() 检查类型，用 int() 或 str() 显式转换。\n练习：记录一个学生的姓名、年龄和是否完成作业，打印它们的类型。",
             "code": 'name = "小知"\nage = 20\nfinished = True\nprint(name, age, type(finished))'},
            {"id": "res-py-2", "name": "学习讲义：条件判断与循环", "kind": "图文讲义", "job_id": None,
             "chapter_id": "ch-2", "content": "print() 将内容输出到屏幕，方便观察程序状态。if / elif / else 根据条件决定执行分支，代码块使用一致缩进。\nfor 遍历序列，range(3) 依次产生 0、1、2。break 提前结束循环，continue 跳过本轮剩余语句。\n练习：依次输出一个成绩列表中达到 60 分的分数，并统计人数。",
             "code": 'scores = [55, 78, 92]\nfor score in scores:\n    if score >= 60:\n        print("通过", score)'},
            {"id": "res-py-3", "name": "学习讲义：组织可复用的程序", "kind": "图文讲义", "job_id": None,
             "chapter_id": "ch-3", "content": "函数用 def 定义，通过参数接收输入，使用 return 把结果交回调用方。打印结果与返回结果是两种不同操作。\n列表按顺序保存元素，索引从 0 开始；字典用键关联值。字典适合描述一个学生，列表适合保存多个学生。\n练习：编写 average(scores) 函数，返回非空成绩列表的平均分。",
             "code": 'def average(scores):\n    return sum(scores) / len(scores)\n\nstudent = {"name": "小知", "scores": [80, 90]}\nprint(average(student["scores"]))'},
        ],
        "questions": [
            question("q1", "ch-1", "kp1", "下面哪一项用于给数据命名并在程序中引用？", ["变量", "函数", "循环", "模块"], 0, "变量名引用对象，例如 age = 20 将名称 age 与整数 20 关联。"),
            question("q2", "ch-1", "kp2", "下面哪个是 Python 的字符串？", ["123", "True", '"学习平台"', "3.14"], 2, "引号包围的是字符串，其他选项分别为整数、布尔值和浮点数。"),
            question("q4", "ch-1", "kp1", "表达式 7 // 2 的结果是什么？", ["3.5", "3", "4", "1"], 1, "// 为向下取整除法，7 // 2 的结果为整数 3。"),
            question("q5", "ch-1", "kp2", '执行 len("Python") 的结果是什么？', ["5", "7", "6", "0"], 2, "len() 返回字符串的字符数，Python 一共 6 个字符。"),
            question("q3", "ch-2", "kp3", "print() 的主要作用是？", ["输出内容", "删除文件", "创建数据库", "安装软件"], 0, "print() 把对象的文本表示输出到标准输出。"),
            question("q6", "ch-2", "kp4", "range(3) 遍历时依次得到哪些数？", ["1、2、3", "0、1、2", "0、1、2、3", "3"], 1, "range 的结束值不包含在序列中，默认从 0 开始。"),
            question("q7", "ch-2", "kp4", "结束当前整个循环应使用哪个关键字？", ["pass", "continue", "break", "def"], 2, "break 结束最内层循环；continue 只跳过当前一轮。"),
            question("q8", "ch-2", "kp4", "Python 用什么划分 if 语句的代码块？", ["花括号", "分号", "缩进", "圆括号"], 2, "Python 通过一致的缩进确定代码块边界。"),
            question("q9", "ch-3", "kp5", "定义函数使用哪个关键字？", ["class", "def", "for", "import"], 1, "def 后跟函数名、参数列表和冒号。"),
            question("q10", "ch-3", "kp5", "把计算结果交回函数调用方使用什么？", ["print", "input", "return", "continue"], 2, "return 返回结果并结束函数；print 只是输出内容。"),
            question("q11", "ch-3", "kp6", "列表 items 的第一个元素如何访问？", ["items[1]", "items[0]", "items[-0.5]", "items.first"], 1, "Python 的列表索引从 0 开始。"),
            question("q12", "ch-3", "kp6", "描述学生的姓名与年龄，哪种结构最适合按字段取值？", ["集合", "字符串", "字典", "浮点数"], 2, "字典以键值对保存信息，可使用 student['name'] 读取姓名。"),
        ],
    }
    deep = {
        "id": "course-deep-learning", "title": "深度学习基础", "teacher": "李老师", "teacher_id": "teacher-demo-2",
        "description": "从神经元、损失函数到训练与泛化，理解一个神经网络如何学习。面向人工智能专业，配套概念讲义和章节自测。",
        "status": "published", "created_at": now, "major_ids": ["m-ai"], "category": "deep-learning",
        "demo": True, "level": "进阶入门", "duration": "约 4 小时",
        "chapters": [{"id": f"dl-ch-{n}", "title": title, "order": n} for n, title in enumerate(
            ["神经网络与前向传播", "损失函数与梯度下降", "模型训练与泛化"], 1)],
        "knowledge_points": [{"id": f"dl-kp-{n}", "name": name} for n, name in enumerate(
            ["神经元与权重", "激活函数", "损失与梯度", "学习率", "过拟合与正则化", "数据集划分"], 1)],
        "resources": [
            {"id": "res-dl-1", "name": "概念讲义：从神经元到多层网络", "kind": "图文讲义", "job_id": None,
             "chapter_id": "dl-ch-1", "content": "一个神经元先计算输入与权重的加权和，再加上偏置。多层网络将上一层的输出传给下一层。\n激活函数引入非线性，否则多层线性变换仍可合并为一层。ReLU 定义为 max(0, x)，保留正值、截断负值。\n思考：如果把所有激活函数去掉，网络的表达能力会受到什么限制？",
             "code": "z = w1 * x1 + w2 * x2 + bias\na = max(0, z)  # ReLU 激活"},
            {"id": "res-dl-2", "name": "概念讲义：一次参数更新发生了什么", "kind": "图文讲义", "job_id": None,
             "chapter_id": "dl-ch-2", "content": "损失函数衡量预测与目标的差异。均方误差常用于回归，交叉熵常用于分类。\n反向传播利用链式法则计算损失对参数的梯度。梯度下降沿梯度反方向更新参数，学习率决定步长。\n学习率过大可能使训练震荡，过小则可能收敛缓慢。先查看损失曲线，再调整训练参数。",
             "code": "# 梯度下降的一步\nweight = weight - learning_rate * gradient"},
            {"id": "res-dl-3", "name": "实验指南：训练集、验证集与测试集", "kind": "实验讲义", "job_id": None,
             "chapter_id": "dl-ch-3", "content": "训练集用于更新参数，验证集用于选择超参数和检查训练过程，测试集用于最终评估。不要用测试集调参。\n训练误差很低但验证误差较高，可能意味着过拟合。可以增加数据、采用正则化或使用早停。\n实验：记录每轮训练与验证损失，找出二者开始明显分离的位置。",
             "code": "for epoch in range(epochs):\n    train_one_epoch(train_data)\n    validation_loss = evaluate(validation_data)"},
        ],
        "questions": [
            question("dl-q1", "dl-ch-1", "dl-kp-1", "神经元中权重主要决定什么？", ["输入的相对影响", "数据集大小", "文件格式", "训练轮数"], 0, "权重决定各输入对神经元加权和的贡献。"),
            question("dl-q2", "dl-ch-1", "dl-kp-2", "激活函数的关键作用是什么？", ["压缩文件", "引入非线性", "划分数据", "清空梯度"], 1, "非线性激活使网络能够表达复杂的非线性关系。"),
            question("dl-q3", "dl-ch-1", "dl-kp-2", "ReLU(-2) 的结果是什么？", ["-2", "2", "0", "1"], 2, "ReLU(x) = max(0, x)，因此负输入输出 0。"),
            question("dl-q4", "dl-ch-1", "dl-kp-1", "前向传播的直接目的是？", ["计算模型预测", "删除参数", "自动标注", "划分测试集"], 0, "前向传播把输入逐层变换为输出预测。"),
            question("dl-q5", "dl-ch-2", "dl-kp-3", "损失函数用来衡量什么？", ["文件大小", "预测与目标的差异", "显卡型号", "标签数量"], 1, "训练通过减小损失来改善预测。"),
            question("dl-q6", "dl-ch-2", "dl-kp-3", "梯度下降沿什么方向更新参数？", ["梯度方向", "随机固定方向", "梯度反方向", "不更新"], 2, "在适当步长下，梯度反方向是局部减小损失的方向。"),
            question("dl-q7", "dl-ch-2", "dl-kp-4", "学习率通常控制什么？", ["参数更新步长", "输入图片颜色", "标签名字", "模型文件路径"], 0, "学习率乘以梯度，决定每次参数更新的步长。"),
            question("dl-q8", "dl-ch-2", "dl-kp-3", "反向传播主要使用哪条数学规则？", ["勾股定理", "链式法则", "求和公式", "概率加法"], 1, "反向传播用链式法则逐层求导。"),
            question("dl-q9", "dl-ch-3", "dl-kp-5", "训练表现好、验证表现差通常提示什么？", ["欠拟合一定消失", "过拟合", "标签全正确", "无需再评估"], 1, "模型可能记住训练集细节而未学到可泛化的规律。"),
            question("dl-q10", "dl-ch-3", "dl-kp-6", "选择超参数主要使用哪个数据集？", ["测试集", "训练集标签副本", "验证集", "任意线上数据"], 2, "验证集用于模型选择，测试集保留给最终评估。"),
            question("dl-q11", "dl-ch-3", "dl-kp-5", "以下哪种方法有助于缓解过拟合？", ["正则化或早停", "反复用测试集调参", "删除验证集", "只看训练准确率"], 0, "正则化约束模型，早停限制过度拟合训练集。"),
            question("dl-q12", "dl-ch-3", "dl-kp-6", "测试集应主要用于什么？", ["每步参数更新", "选择所有超参数", "补写训练标签", "最终泛化评估"], 3, "保持测试集独立，可以更可信地评估未见数据上的表现。"),
        ],
    }
    return [python, deep]


def upgrade_catalog(data):
    """Add and enrich demo objects once, preserving user-created and edited content."""
    if data.get("demo_catalog_version", 0) >= 2:
        return False
    existing = {course["id"]: course for course in data["courses"]}
    for sample in demo_courses():
        course = existing.get(sample["id"])
        if course is None:
            data["courses"].append(sample)
            continue
        # Existing example questions, chapters, jobs and saved submissions retain their IDs.
        for field in ("chapters", "questions", "knowledge_points", "resources"):
            objects = course.setdefault(field, [])
            ids = {item["id"] for item in objects}
            objects.extend(deepcopy(item) for item in sample[field] if item["id"] not in ids)
            if field == "resources":
                reference = {item["id"]: item for item in sample[field]}
                for item in objects:
                    if item["id"] in reference and not item.get("job_id"):
                        for key in ("content", "code"):
                            item.setdefault(key, reference[item["id"]][key])
            elif field == "questions":
                reference = {item["id"]: item for item in sample[field]}
                for item in objects:
                    source = reference.get(item["id"])
                    legacy_text = "Python 中用于保存数据的命名空间通常称为？"
                    if (source and item.get("text") in (source["text"], legacy_text)
                            and item.get("options") == source["options"] and item.get("answer") == source["answer"]):
                        item.setdefault("explanation", source["explanation"])
        for field in ("category", "demo", "duration", "level"):
            course.setdefault(field, sample[field])
        if course["id"] == "course-demo" and data.get("demo_teaching_classes_version", 0) < 2:
            course["major_ids"] = list(sample["major_ids"])
            course.pop("major_id", None)
    data["demo_catalog_version"] = 2
    return True


def upgrade_programming_samples(data):
    """Append samples once, preserving edits, deletions after migration and all records."""
    if data.get("demo_programming_version", 0) >= 1:
        return False
    course = next((item for item in data["courses"] if item["id"] == "course-demo"), None)
    if course is not None and course.get("chapters"):
        questions = course.setdefault("programming_questions", [])
        ids = {item["id"] for item in questions}
        chapter_ids = {item["id"] for item in course["chapters"]}
        for sample in demo_programming_questions():
            if sample["id"] not in ids:
                # Keep questions reachable if the teacher has reorganized the demo chapters.
                if sample["chapter_id"] not in chapter_ids:
                    sample["chapter_id"] = course["chapters"][0]["id"]
                questions.append(sample)
    data["demo_programming_version"] = 1
    return True


def seed_activity(data, accounts):
    """Persist clearly tagged sample activity only for untouched built-in demo students."""
    if data.get("demo_activity_version", 0) >= 2 and data.get("demo_assignment_version", 0) >= 1:
        return False
    courses = {course["id"]: course for course in data["courses"]}
    now = datetime.now(timezone.utc)
    active = {record["student_id"] for record in data["submissions"]}
    active.update(record["student_id"] for record in data["wrong_questions"])
    plans = {1: [3, 4, 2], 2: [3, 2, 3], 3: [2, 3], 4: [4, 4, 4],
             5: [1, 2, 2], 6: [3, 2], 7: [], 8: [1, 1]}
    for student in accounts:
        if not student.get("demo") or student["role"] != "student" or student["id"] in active:
            continue
        number = next((n for n in plans if student["id"] == f"student-demo-{n}"), None)
        if number is None:
            continue
        chosen = [course for course in courses.values() if course.get("demo") and course.get("status") == "published"
                  and student.get("major_id") in course["major_ids"]]
        for course in chosen:
            points = {point["id"]: point["name"] for point in course["knowledge_points"]}
            for ci, correct_count in enumerate(plans[number]):
                chapter = course["chapters"][ci % len(course["chapters"])]
                questions = [q for q in course["questions"] if q.get("chapter_id") == chapter["id"]]
                if not questions:
                    continue
                details = []
                stamp = (now - timedelta(days=6-ci, hours=number)).isoformat()
                for qi, q in enumerate(questions):
                    selected = q["answer"] if qi < correct_count else (q["answer"] + 1) % len(q["options"])
                    detail = {"question_id": q["id"], "selected": selected, "correct": selected == q["answer"],
                              "answer": q["answer"], "text": q["text"], "options": list(q["options"]),
                              "chapter_id": chapter["id"], "knowledge_point_name": points.get(q.get("knowledge_point_id"), "未分类"),
                              "explanation": q.get("explanation", "")}
                    details.append(detail)
                    if not detail["correct"]:
                        mastered = qi == len(questions)-1 and number % 2 == 0
                        reviewed = bool(qi % 2)
                        last_reviewed = now - timedelta(days=1)
                        next_at = last_reviewed + timedelta(days=2) if reviewed else datetime.fromisoformat(stamp) + timedelta(days=1)
                        data["wrong_questions"].append({
                            "id": f"demo-wrong-{number}-{q['id']}", "student_id": student["id"],
                            "course_id": course["id"], "question_id": q["id"], "knowledge_point_id": q.get("knowledge_point_id"),
                            "knowledge_point_name": detail["knowledge_point_name"], "chapter_id": chapter["id"],
                            "chapter_title": chapter["title"], "question_snapshot": {key: detail[key] for key in ("text", "options", "answer", "explanation")},
                            "wrong_count": 1, "last_wrong_at": stamp, "first_wrong_at": stamp,
                            "status": "mastered" if mastered else "unresolved", "review_interval_days": 2 if reviewed else 1,
                            "next_review_at": next_at.isoformat(), "review_count": 1 if reviewed else 0,
                            **({"last_reviewed_at": last_reviewed.isoformat(), "last_review_result": True} if reviewed else {}),
                            **({"mastered_at": (now - timedelta(hours=12)).isoformat()} if mastered else {}),
                            "demo": True,
                        })
                correct = sum(detail["correct"] for detail in details)
                data["submissions"].append({
                    "id": f"demo-sub-{number}-{course['id']}-{ci}", "student_id": student["id"], "student": student["name"],
                    "course_id": course["id"], "chapter_id": chapter["id"], "submitted_at": stamp,
                    "score": round(correct / len(details)*100), "correct_count": correct, "total": len(details),
                    "details": details, "demo": True,
                })
    data["submissions"].sort(key=lambda record: record["submitted_at"], reverse=True)
    from .demo_assignments import prepare_demo_assignments
    prepare_demo_assignments(data, accounts)
    data["demo_activity_version"] = 2
    return True
