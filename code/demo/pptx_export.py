"""Export extracted JPEGs as one original image per PowerPoint slide."""
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.util import Inches


def create_pptx(images: list[Path], destination: Path):
    if not images:
        raise ValueError("没有可导出的课件图片，请先完成本地预处理。")
    presentation = Presentation()
    with Image.open(images[0]) as source:
        width, height = source.size
    # PowerPoint uses a single canvas size; match the first frame, including portrait videos.
    scale = 13.333333 / max(width, height)
    presentation.slide_width = Inches(max(1, width * scale))
    presentation.slide_height = Inches(max(1, height * scale))
    presentation.core_properties.title = "课程课件"
    presentation.core_properties.author = "知学 · 智慧学习平台"
    presentation.core_properties.subject = "按视频提取顺序排列，每页一张原始课件截图"
    for image in images:
        with Image.open(image) as source:
            width, height = source.size
        ratio = min(presentation.slide_width / width, presentation.slide_height / height)
        fitted_width, fitted_height = round(width * ratio), round(height * ratio)
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_picture(str(image), (presentation.slide_width - fitted_width) // 2,
                                 (presentation.slide_height - fitted_height) // 2,
                                 width=fitted_width, height=fitted_height)
    # Failed/interrupted saves must not leave a downloadable, incomplete ZIP package.
    temporary = destination.with_suffix(".pptx.tmp")
    try:
        presentation.save(str(temporary))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
