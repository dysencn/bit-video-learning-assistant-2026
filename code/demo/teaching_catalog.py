"""Six labelled teaching instances across three subjects, each with one student major."""
from copy import deepcopy
from datetime import datetime, timezone

from .demo_catalog import demo_courses


def _subject(cid, title, code, teacher, majors, description, chapters):
    now = datetime.now(timezone.utc).isoformat()
    course = {"id": cid, "title": title, "course_code": code,
              "teaching_class": "2026级 01班", "term": "2026年秋季",
              "teacher": "王老师" if teacher == 1 else "李老师", "teacher_id": f"teacher-demo-{teacher}",
              "major_ids": majors, "description": description, "status": "published", "demo": True,
              "category": "web" if teacher == 1 else "machine-learning", "level": "基础", "duration": "约 4 小时",
              "created_at": now, "updated_at": now, "chapters": [], "resources": [],
              "knowledge_points": [], "questions": [], "programming_questions": []}
    for ci, (name, content, example, questions) in enumerate(chapters, 1):
        chapter_id, point_id = f"{cid}-ch{ci}", f"{cid}-kp{ci}"
        course["chapters"].append({"id": chapter_id, "title": name, "order": ci})
        course["knowledge_points"].append({"id": point_id, "name": name})
        course["resources"].append({"id": f"{cid}-res{ci}", "chapter_id": chapter_id,
            "name": f"学习讲义：{name}", "kind": "图文讲义", "job_id": None, "content": content, "code": example})
        for qi, (text, options, answer, explanation) in enumerate(questions, 1):
            course["questions"].append({"id": f"{cid}-q{ci}-{qi}", "chapter_id": chapter_id,
                "knowledge_point_id": point_id, "text": text, "options": options,
                "answer": answer, "explanation": explanation})
    return course


def _second_class(source, cid):
    course = deepcopy(source)
    course.update(id=cid, teaching_class="2026级 02班", status="draft")
    # Each teaching class has independent content IDs, so assignments and grades cannot mix.
    mapping = {item["id"]: f"{cid}-{item['id']}" for field in
               ("chapters", "resources", "questions", "knowledge_points", "programming_questions")
               for item in course.get(field, [])}
    for field in ("chapters", "resources", "questions", "knowledge_points", "programming_questions"):
        for item in course.get(field, []):
            item["id"] = mapping[item["id"]]
            for key in ("chapter_id", "knowledge_point_id"):
                if item.get(key) in mapping:
                    item[key] = mapping[item[key]]
    return course


def _previous_samples():
    python, deep = demo_courses()
    for course, code in ((python, "PY101"), (deep, "DL101")):
        course.update(course_code=code, teaching_class="2026级 01班", term="2026年秋季")
    web = _subject("course-web-foundations", "Web 开发基础", "WEB101", 1, ["m-soft", "m-cs"],
        "从 HTML 结构、CSS 布局到 JavaScript 交互，完成一个可访问的课程卡片页面。", [
        ("HTML 与语义化", "HTML 描述页面结构。标题、导航、正文应选择合适的语义标签，样式交给 CSS。\n表单控件使用 label 建立关联，图片提供有意义的替代文字。\n练习：制作课程介绍页，包含标题、课程说明、章节列表与报名按钮。",
         '<article>\n  <h2>Web 开发基础</h2>\n  <p>学习结构、样式与交互。</p>\n  <button type="button">查看章节</button>\n</article>', [
            ("HTML 主要负责网页的哪一部分？", ["结构与语义", "数据库备份", "网络路由", "程序编译"], 0, "HTML 组织网页内容并表达其语义。"),
            ("表达导航区域应优先使用哪个标签？", ["span", "nav", "br", "i"], 1, "nav 表示主要导航链接的区域。"),
            ("将文字说明关联到表单输入框，应使用什么？", ["随机空格", "背景图片", "label", "注释"], 2, "label 的 for 与输入框的 id 可建立明确关联。"),
            ("信息性图片的替代文字应放在哪个属性？", ["href", "src", "class", "alt"], 3, "alt 提供图片无法显示或使用读屏时的文字说明。")]),
        ("CSS 盒模型与布局", "CSS 盒模型包含内容、内边距、边框和外边距。border-box 将内边距和边框计入指定宽度。\nFlex 适合一维排列，Grid 适合二维网格。媒体查询可在窄屏调整列数。\n练习：将课程卡片在桌面排成三列，在手机排成一列，并检查页面是否横向溢出。",
         '.courses { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }\n@media (max-width: 600px) { .courses { grid-template-columns: 1fr; } }', [
            ("CSS 的 padding 表示什么？", ["内边距", "外边距", "字体大小", "边框颜色"], 0, "padding 是内容区域与边框之间的空间。"),
            ("需要同时控制行和列的布局，通常选择什么？", ["float 标题", "CSS Grid", "alt 属性", "JSON"], 1, "Grid 提供二维布局能力。"),
            ("针对不同屏幕宽度调整样式，常使用什么？", ["数据库查询", "HTML 注释", "媒体查询", "文件压缩"], 2, "媒体查询可按视口条件应用样式。"),
            ("border-box 模式下，指定 width 通常包含哪些部分？", ["只有文字", "整个屏幕", "外边距", "内容、内边距与边框"], 3, "border-box 将内容、padding 和 border 计入 width。")]),
        ("JavaScript 事件与交互", "JavaScript 可以响应用户操作并更新页面。使用 addEventListener 监听事件，使用 textContent 安全地显示普通文字。\n表单提交前先检查必填内容；异步请求失败时显示提示并允许重试。\n练习：为课程列表增加关键词搜索，搜索时保留输入焦点，并显示匹配结果数量。",
         'const input = document.querySelector("input");\ninput.addEventListener("input", event => {\n  console.log(event.target.value);\n});', [
            ("注册按钮点击事件常使用哪个方法？", ["addEventListener", "sortTable", "printCSS", "compileHTML"], 0, "addEventListener 把事件处理函数绑定到元素。"),
            ("显示用户输入的普通文本，优先使用哪个属性？", ["innerHTML", "textContent", "outerHTML", "document.write"], 1, "textContent 将内容作为文本显示，而不解释为 HTML。"),
            ("关键词输入时实时筛选，常监听哪个事件？", ["resize", "load", "input", "unload"], 2, "input 会在用户改变输入内容时触发。"),
            ("异步加载失败后，合理的界面行为是什么？", ["永久禁用页面", "伪造成功结果", "清空所有资料", "提示原因并提供重试"], 3, "明确失败状态并允许重试，用户才能继续操作。")]),
    ])
    machine = _subject("course-machine-learning", "机器学习基础", "ML101", 2, ["m-ai"],
        "从数据与特征出发，理解监督学习、模型评估与常见的数据泄漏问题。", [
        ("数据、特征与标签", "监督学习根据带标签的样本学习输入与目标之间的关系。特征是输入描述，标签是需要预测的目标。\n分类预测离散类别，回归预测连续数值。数据质量会直接影响模型表现。\n练习：为房价预测列出面积、位置等特征，并说明预测目标与可能缺失的数据。",
         'features = [[60, 2], [90, 3], [120, 4]]\nprices = [180, 270, 360]\nprint(len(features), len(prices))', [
            ("监督学习样本中的标签通常表示什么？", ["预测目标", "文件路径", "学习率", "模型版本"], 0, "标签是样本对应的真实目标值。"),
            ("预测房屋价格通常属于哪类任务？", ["分类", "回归", "排序文件", "图像压缩"], 1, "房价是连续数值，通常按回归任务处理。"),
            ("判断邮件是否为垃圾邮件通常属于什么任务？", ["回归", "数据备份", "分类", "文件解码"], 2, "输出为离散类别，因此属于分类。"),
            ("输入模型的房屋面积一般属于什么？", ["标签一定值", "损失函数", "测试集名称", "特征"], 3, "特征用于描述模型的输入样本。")]),
        ("训练、验证与预处理", "训练集用于拟合模型，验证集用于选择方案，测试集用于最终评估。\n归一化等预处理参数只用训练集估计，再应用到验证和测试集，避免数据泄漏。\n练习：按时间划分一份销售数据，说明为什么不能用未来数据预测过去。",
         'train = [2, 4, 6]\nmean = sum(train) / len(train)\nvalidation = [8]\nprint([x - mean for x in validation])', [
            ("模型参数主要通过哪个集合进行拟合？", ["训练集", "测试集", "线上全部未来数据", "提交报告"], 0, "训练集直接参与模型拟合。"),
            ("用于选择超参数的数据通常是什么？", ["最终测试集", "验证集", "任意未授权数据", "模型文件名"], 1, "验证集用于模型和超参数选择。"),
            ("归一化参数应首先从哪部分数据计算？", ["全部未来样本", "测试标签", "训练集", "最终评估报告"], 2, "只在训练集估计预处理参数可减少数据泄漏。"),
            ("时间序列预测中，随机混合过去与未来样本的主要风险是什么？", ["数据一定变少", "无法保存模型", "标签自动消失", "未来信息泄漏"], 3, "训练时使用未来信息会导致评估过于乐观。")]),
        ("模型评估与泛化", "评估指标应与任务目标一致。类别不均衡时，仅看准确率可能掩盖少数类别识别失败。\n训练表现好但验证表现差，可能是过拟合；交叉验证可帮助估计模型对不同数据划分的稳定性。\n练习：对疾病筛查的模拟预测结果计算召回率，并解释漏检的影响。",
         'tp, fn = 8, 2\nrecall = tp / (tp + fn)\nprint(f"recall={recall:.2f}")', [
            ("训练表现好而验证表现差，通常需要排查什么？", ["过拟合", "图片颜色", "文件扩展名", "用户名长度"], 0, "模型可能记住训练样本而没有获得良好泛化能力。"),
            ("召回率的计算式是哪一项？", ["TN / 全部样本", "TP / (TP + FN)", "FP / TP", "FN / TN"], 1, "召回率表示真实正例中被正确识别的比例。"),
            ("类别极不均衡时，只看准确率可能遗漏什么？", ["模型文件名", "运行日期", "少数类别的识别效果", "图片分辨率"], 2, "高准确率也可能伴随少数类别几乎全部漏检。"),
            ("独立测试集的主要用途是什么？", ["每一步训练", "反复选择超参数", "补齐训练标签", "最终泛化评估"], 3, "保持测试集独立，才能较可信地评估未见数据。")]),
    ])
    return [python, _second_class(python, "course-python-02"), web,
            deep, _second_class(deep, "course-deep-learning-02"), machine]


def teaching_samples():
    python, python_ai, web, deep, deep_second, _ = _previous_samples()
    python.update(major_ids=["m-soft"], teaching_class="软件工程 01班")
    python_ai.update(major_ids=["m-ai"], teaching_class="人工智能 01班", status="published")
    web.update(major_ids=["m-soft"], teaching_class="软件工程 01班")
    deep.update(major_ids=["m-ai"], teaching_class="人工智能 01班")
    deep_second.update(major_ids=["m-ai"], teaching_class="人工智能 02班")
    python_cs = _second_class(python, "course-python-cs")
    python_cs.update(major_ids=["m-cs"], teaching_class="计算机科学 01班", status="published",
                     teacher="李老师", teacher_id="teacher-demo-2")
    return [python, python_ai, web, deep, deep_second, python_cs]


def upgrade_teaching_samples(data):
    """Upgrade sample classes by major without discarding user content or real history."""
    if data.get("demo_teaching_classes_version", 0) >= 2:
        return False
    existing = {course["id"]: course for course in data["courses"]}
    old_names = {course["id"]: course["teaching_class"] for course in _previous_samples()}
    old_ml = existing.get("course-machine-learning")
    if old_ml and old_ml.get("demo"):
        reference = _previous_samples()[-1]
        keys = ("title", "description", "category", "questions", "resources", "chapters", "knowledge_points",
                "programming_questions", "teaching_class", "term", "course_code", "major_ids", "teacher_id",
                "level", "duration", "status")
        used = any(item.get("course_id") == old_ml["id"] for field in
                   ("submissions", "wrong_questions", "assignments") for item in data.get(field, []))
        if not used and all(old_ml.get(key) == reference.get(key) for key in keys):
            data["courses"].remove(old_ml)
        else:
            old_ml["archived_from_status"] = old_ml.get("status", "published")
            old_ml["status"] = "archived"
    for sample in teaching_samples():
        current = existing.get(sample["id"])
        if current is None:
            data["courses"].append(sample)
            continue
        for key in ("course_code", "term"):
            current.setdefault(key, sample[key])
        if current.get("teaching_class") in (None, "", old_names.get(sample["id"])):
            current["teaching_class"] = sample["teaching_class"]
        current["major_ids"] = sample["major_ids"]
        if sample["id"] == "course-python-02" and current.get("status") == "draft":
            current["status"] = "published"
    data["demo_teaching_classes_version"] = 2
    return True
