# -*- coding: utf-8 -*-
import os
from pptx import Presentation
BASE = r"C:\Users\Lenovo\Desktop\bit-video-learning-assistant-2026-main\bit-video-learning-assistant-2026-main"
SRC = os.path.join(BASE, "第3组20261118汇报.pptx")
prs = Presentation(SRC)
print("slides:", len(prs.slides))
for idx, s in enumerate(prs.slides, start=1):
    print("\n" + "=" * 60)
    print(f"[P{idx}]")
    for sh in s.shapes:
        if sh.has_table:
            print("  TABLE:")
            for row in sh.table.rows:
                cells = [c.text.replace("\n", " / ") for c in row.cells]
                print("   | " + " | ".join(cells))
        elif sh.has_text_frame:
            t = sh.text_frame.text.strip()
            if t:
                print("  TXT:", t.replace("\n", " / "))
        elif sh.shape_type == 13:
            print("  PIC")
    if s.has_notes_slide:
        n = s.notes_slide.notes_text_frame.text.strip()
        if n:
            print("  NOTE:", n.replace("\n", " / "))
