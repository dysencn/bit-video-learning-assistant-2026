# -*- coding: utf-8 -*-
"""生成周妍个人部分（第3组20261118汇报 P14–20）演讲稿 DOCX，十分钟口径。"""
import os
import re
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH as PP_ALIGN
from docx.oxml.ns import qn

BASE = r"C:\Users\Lenovo\Desktop\bit-video-learning-assistant-2026-main\bit-video-learning-assistant-2026-main"
OUT = os.path.join(BASE, "第3组20261118汇报_周妍个人部分演讲稿_14-20页.docx")

HEI = "黑体"
SONG = "宋体"
KAI = "楷体"
TNR = "Times New Roman"

OPENING = "各位老师、同学好，下面由我汇报教师端本周的进展。我的部分对应 PPT 第 14 到 20 页，包括内容管理闭环、作业发布与管理、成绩册、课程公告，以及本周验收情况和下一步安排。先说明口径：演示界面来自隔离演示账号和已保存的真实 API 验收产物，演示分数和人数不代表真实教学成效。"

PAGES = [
    ("第 14 页｜3.1 周妍 · 教师端内容管理闭环",
     "首先我完成了教师端的内容管理闭环，把课程、章节、学习资源和测试题目四类对象都补上了可编辑操作，教师端从原来的能发布，扩展到可以持续维护教学内容。课程方面，可以编辑名称、简介、分类、难度和学时，字段带有必填和长度校验；支持一键复制课程，副本标题会加上“（副本）”，章节、资源和题目随副本一起保存；课程还支持归档和恢复，归档后学生端不可访问，但历史提交记录会保留，需要时可以恢复到归档前的状态。另外提供学生视角预览，发布前先按学生端可见范围检查排版。章节可以新增、重命名，也可以上移、下移调整顺序；删除含有内容的章节时，可以选择把题目和资源迁移到其他章节，或者确认级联删除；如果章节里有内容，却既没有选择迁移也没有选择级联，服务端会拒绝删除。学习资源可以编辑名称、类型、所属章节、讲义正文和代码示例，也可以删除，删除后保持章节关联正确，避免出现孤立资料。测试题目分单选题和编程题，支持编辑、上下排序，以及停用和恢复；停用属于软删除，对学生端隐藏，但教师侧保留历史，历史提交快照不受影响。验收上，修改章节或题目后学生刷新即可见，停用题目不会下发给学生，归档课程学生端不可见。这四类对象都会校验教师归属，其他教师不能改动本人课程之外的内容，软删除也只保留历史快照，不会重写既有提交。"),
    ("第 15 页｜3.2 作业发布：从练习到可运营任务",
     "第二部分是作业发布功能。每份作业独立出题，题目随作业单独保存快照；已有提交之后，题目和次数就会锁定，不再改动已保存的记录。时间上分为开放、截止、关闭三段，可以设置最多提交次数；同时支持作答时限、允许迟交天数和每天扣分规则，成绩发布方式分即时发布和手动发布，作业本身有草稿和已发布两种状态，草稿不对学生开放，发布后学生才能从作业页进入作答。学生提交后先看到得分，次数用尽后才解锁标准答案和解析；教师可以查看已交、未交和迟交情况，以及未交名单，并可以一键提醒未交学生；提交记录会保留作答时长等字段，方便后续复盘。这里也说明当前边界：本周作业题型还是单选，自由组卷、编程题作业和作业级学情趋势列入下一步计划。概括来说，一份作业就是独立题目、时间窗、次数时限和迟交规则这几项的组合。"),
    ("第 16 页｜作业管理页面展示",
     "这一页是作业管理页面的实际界面，配合上一页的规则一起说明。页面左侧是作业列表，每份作业都能区分草稿和已发布状态，教师在这里维护题目范围、开放和截止时间、提交次数、时限和迟交策略。右侧是提交概览：已交、未交、迟交人数和平均分，以及未交学生名单，教师可以直接从这里“提醒未交”。学生从作业页进入作答后，服务端记录每一次尝试，提交时校验时间窗、剩余次数、作答时限和迟交扣分。选择手动发布成绩的作业，在教师发布之前，学生在学习记录、错题本和 AI 分析里都看不到成绩和逐题结果，发布之后才恢复可见。这个页面就是前面规则的落点：教师在一个入口完成发布、追踪和提醒。"),
    ("第 17 页｜3.3 成绩册：把结果变成可带走的数据",
     "我实现的下一个功能是成绩册，目标是把学生作业成绩变成可以带走的数据。成绩册只汇总已发布作业，按学生乘以作业成表；取值方式支持最近一次、最高分和首次三种，可以按章节、提交状态筛选，也可以限定日期范围，页面显示平均分、提交项数和迟交标记，并汇总学生数、已交学生、平均分和及格率。导出方面，成绩册支持导出 CSV，文件带 UTF-8 BOM，用 Excel 直接打开中文不会乱码；可以先按章节筛选，再导出当前范围的表格，文件名按课程命名。下钻和权限上，教师可以逐格查看，点击学生姓名进入个人详情，查看四维雷达、作业情况、练习提交、错题和掌握情况；页面只返回本人课程范围的学生，非本人课程范围不可访问，其他教师不能查看。这里需要表明的是，自主练习、错题复习和 AI 自测不混入作业成绩；三种取值只是展示口径，不会修改原始提交记录，导出和页面也使用同一套归属与答案解锁规则，不能通过下载提前获取标准答案。"),
    ("第 18 页｜3.4 课程公告与提醒",
     "还新增了课程公告与提醒功能。公告包含标题、正文、受众和置顶；受众分全体学生、未提交学生和需关注学生三类。收件人由服务端按课程范围和提交情况计算，不接受前端传入的名单，也不能指定某位学生单独收件。学生侧导航会显示未读红点，工作台展示最新公告，公告页支持单条标记已读和全部标记已读；教师端可以看到收件人数和已读人数，也可以删除公告。收件人快照在发送时生成；作业管理页的提醒未交功能，复用的就是这一套公告能力，不依赖外部邮件服务。连续验收的链路是：教师发布提醒完成作业的公告，学生标记已读并提交作业，教师查看成绩并导出 CSV；同时还要验证其他教师不能管理这门课，其他学生不能读取不属于自己的内容，没有作业或没有提交时也不显示虚构分数。"),
    ("第 19 页｜公告页面展示",
     "这一页是公告功能的实际界面。左侧是教师端的公告列表和发布入口：教师填写标题、正文，选择受众，可以设置置顶；列表里能看到每条公告的收件人数和已读人数，也可以删除公告。右侧是学生端看到的效果：导航上有未读红点，工作台展示最新公告，进入公告页可以单条或全部标记已读。这里再强调一次受众规则：三类受众都由服务端按课程范围和提交情况计算，前端不能传名单，保证公告只发给应该收到的学生；作业管理页的“提醒未交”，生成的就是面向未提交学生的同一类公告，提醒和已读回执走的是同一条链路。"),
    ("第 20 页｜3.5 连续验收与下一步",
     "最后一页是本周连续验收情况和下一步。测试方面，仓库共有 156 项自动化测试通过，其中教师端功能测试 13 项，覆盖 4 条教学链路闭环。本周已经闭环的内容是：内容可改、可排序、可停用与恢复；作业有独立题目、时间窗和迟交规则；成绩册可筛选、可下钻、可导出 CSV；公告可定向、可回执、可提醒未交。下一步计划包括三项：一是作业级学情查看与薄弱知识点讲评，也就是跨多次作业看班级和学生的成绩走向，并按整次作业汇总正确率最低的题目、共性错误和需要重讲的知识点，供教师讲评参考；二是自由组卷、更多题型和编程题作业；三是作业批改、导出与权限细分。总结一句，教师端已经从“能发布”，走到“能维护、能布置、能导出、能沟通”。需要说明的是，验收使用的是隔离演示数据，演示分数和人数不代表真实教学成效。"),
]

CLOSING = "以上就是我负责的教师端部分，下面由其他同学继续汇报。"


def set_run(run, font_cn, size, bold=False, color=RGBColor(0, 0, 0)):
    run.font.name = TNR
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    el = rPr.find(qn("w:rFonts"))
    if el is None:
        el = rPr.makeelement(qn("w:rFonts"), {})
        rPr.append(el)
    el.set(qn("w:eastAsia"), font_cn)
    el.set(qn("w:ascii"), TNR)
    el.set(qn("w:hAnsi"), TNR)


def add_body(doc, text):
    p = doc.add_paragraph()
    p.alignment = PP_ALIGN.JUSTIFY
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.first_line_indent = Pt(24)
    pf.space_after = Pt(6)
    run = p.add_run(text)
    set_run(run, SONG, 12)
    return p


def main():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.5)
    sec.left_margin = sec.right_margin = Cm(2.5)

    body_total = sum(len(re.findall(r"[\u4e00-\u9fff]", b)) for _, b in PAGES)
    total = body_total
    total += len(re.findall(r"[\u4e00-\u9fff]", OPENING))
    total += len(re.findall(r"[\u4e00-\u9fff]", CLOSING))
    low, high = total / 200, total / 180

    t = doc.add_paragraph()
    t.alignment = PP_ALIGN.CENTER
    r = t.add_run("周妍 · 个人部分演讲稿（第 14–20 页）")
    set_run(r, HEI, 18, bold=True)
    t.paragraph_format.space_after = Pt(10)

    sub = doc.add_paragraph()
    sub.alignment = PP_ALIGN.CENTER
    rs = sub.add_run(f"对应《第3组20261118汇报》PPT 第 14–20 页（3.1–3.5）　｜　正文约 {total} 字，按 180–200 字/分钟估算约 {low:.1f}–{high:.1f} 分钟")
    set_run(rs, KAI, 12)
    sub.paragraph_format.space_after = Pt(8)

    note = doc.add_paragraph()
    note.alignment = PP_ALIGN.CENTER
    rn = note.add_run("口径说明：演示界面来自隔离演示账号和已保存的真实 API 验收产物，演示分数和人数不代表真实教学成效。")
    set_run(rn, KAI, 10.5)
    note.paragraph_format.space_after = Pt(12)

    add_body(doc, OPENING)

    for head, body in PAGES:
        h = doc.add_paragraph()
        rh = h.add_run(head)
        set_run(rh, HEI, 14, bold=True)
        h.paragraph_format.space_before = Pt(10)
        h.paragraph_format.space_after = Pt(4)
        add_body(doc, body)

    add_body(doc, CLOSING)

    doc.save(OUT)
    print("saved", OUT)
    print("body_cjk=", body_total, "total_cjk=", total, f"time={low:.1f}-{high:.1f}min")


if __name__ == "__main__":
    main()
