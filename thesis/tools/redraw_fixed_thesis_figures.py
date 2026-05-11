from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures" / "redrawn_fixed"
OUT.mkdir(parents=True, exist_ok=True)

FONT_SERIF = Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf")
FONT_SANS = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")


def font(size: int, bold: bool = False, serif: bool = True) -> ImageFont.FreeTypeFont:
    # Noto variable fonts keep Chinese and ASCII glyph metrics consistent, which
    # avoids the baseline drift that caused the original LaTeX-rendered figures
    # to overlap after DOCX/PDF conversion.
    return ImageFont.truetype(str(FONT_SERIF if serif else FONT_SANS), size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.multiline_textbbox((0, 0), text, font=fnt, spacing=7, align="center")
    return box[2] - box[0], box[3] - box[1]


def centered_text(draw: ImageDraw.ImageDraw, xyxy, text: str, fnt, fill="#111827", spacing=7) -> None:
    x1, y1, x2, y2 = xyxy
    w, h = text_size(draw, text, fnt)
    draw.multiline_text(
        ((x1 + x2 - w) / 2, (y1 + y2 - h) / 2 - 2),
        text,
        font=fnt,
        fill=fill,
        spacing=spacing,
        align="center",
    )


def rounded_box(draw, xyxy, text, fill, outline="#4b5563", radius=10, width=2, fnt=None):
    draw.rounded_rectangle(xyxy, radius=radius, fill=fill, outline=outline, width=width)
    centered_text(draw, xyxy, text, fnt or font(28))


def rect_box(draw, xyxy, text, fill, outline="#4b5563", width=2, fnt=None, dashed=False):
    if dashed:
        x1, y1, x2, y2 = xyxy
        dash = 10
        for x in range(int(x1), int(x2), dash * 2):
            draw.line((x, y1, min(x + dash, x2), y1), fill=outline, width=width)
            draw.line((x, y2, min(x + dash, x2), y2), fill=outline, width=width)
        for y in range(int(y1), int(y2), dash * 2):
            draw.line((x1, y, x1, min(y + dash, y2)), fill=outline, width=width)
            draw.line((x2, y, x2, min(y + dash, y2)), fill=outline, width=width)
        draw.rectangle((x1 + 1, y1 + 1, x2 - 1, y2 - 1), fill=fill)
    else:
        draw.rectangle(xyxy, fill=fill, outline=outline, width=width)
    centered_text(draw, xyxy, text, fnt or font(26))


def arrow(draw, start, end, fill="#374151", width=2):
    draw.line((*start, *end), fill=fill, width=width)
    sx, sy = start
    ex, ey = end
    if abs(ex - sx) >= abs(ey - sy):
        sign = 1 if ex > sx else -1
        pts = [(ex, ey), (ex - sign * 13, ey - 7), (ex - sign * 13, ey + 7)]
    else:
        sign = 1 if ey > sy else -1
        pts = [(ex, ey), (ex - 7, ey - sign * 13), (ex + 7, ey - sign * 13)]
    draw.polygon(pts, fill=fill)


def line_label(draw, xy, text, fnt=None):
    fnt = fnt or font(20)
    w, h = text_size(draw, text, fnt)
    x, y = xy
    draw.rectangle((x - 4, y - 2, x + w + 4, y + h + 2), fill="white")
    draw.text((x, y), text, font=fnt, fill="#111827")


def architecture():
    img = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(img)
    layer_f = font(34)
    module_f = font(26)
    layers = [
        (70, 50, 1270, 140, "#E7E7FA", "展示层", [("Vue 3\n前端", 160), ("FastAPI\n后端", 760), ("RESTful\nAPI", 1230)]),
        (70, 220, 1270, 310, "#E2F7E2", "治理层", [("策略评分\n与治理", 420), ("总报告\n整合", 980)]),
        (70, 390, 1270, 480, "#FFFBE4", "调度层", [("固定阈值\n调度", 170), ("滚动优化\n调度", 455), ("电池退化\n分析", 725), ("神经策略\n蒸馏", 1040)]),
        (70, 560, 1270, 650, "#F8E8D6", "预测层", [("LightGBM\n主模型", 190), ("深度学习\n对比模型", 570), ("物理边界\n裁剪", 1040)]),
        (70, 730, 1270, 820, "#FBE1E1", "数据层", [("数据采集\n与标准化", 190), ("数据清洗\n质量报告", 570), ("特征工程\n数据集", 1040)]),
    ]
    label_lanes = {
        "展示层": (455, 50, 625, 140),
        "治理层": (610, 220, 780, 310),
        "调度层": (610, 390, 780, 480),
        "预测层": (645, 560, 815, 650),
        "数据层": (645, 730, 815, 820),
    }
    for x1, y1, x2, y2, fill, label, mods in layers:
        d.rectangle((x1, y1, x2, y2), fill=fill, outline="#4b5563", width=2)
        # Put the layer title in an open lane rather than the geometric center;
        # this keeps arrows and module boxes from covering Chinese text.
        centered_text(d, label_lanes[label], label, layer_f)
        for txt, cx in mods:
            rounded_box(d, (cx - 95, y1 - 1, cx + 95, y2 + 1), txt, "white", fnt=module_f)
    for y1, y2 in [(730, 650), (560, 480), (390, 310), (220, 140)]:
        arrow(d, (670, y1), (670, y2), width=2)
    img.save(OUT / "fig4-1_architecture_fixed.png", quality=95)


def er():
    img = Image.new("RGB", (1150, 960), "white")
    d = ImageDraw.Draw(img)
    title_f = font(34)
    body_f = font(24)
    small_f = font(24)
    users = (80, 80, 390, 450)
    tasks = (710, 50, 1020, 480)
    reports = (420, 560, 730, 900)
    for box, title, rows in [
        (users, "users", ["id (PK)", "username", "password_hash", "role", "created_at"]),
        (tasks, "async_tasks", ["id (PK)", "user_id (FK)", "type", "status", "started_at", "finished_at"]),
        (reports, "report_meta", ["id (PK)", "path", "status", "created_at"]),
    ]:
        d.rectangle(box, fill="#F0F1FC", outline="#4b5563", width=2)
        centered_text(d, (box[0], box[1], box[2], box[1] + 70), title, title_f)
        d.line((box[0] + 25, box[1] + 105, box[2] - 25, box[1] + 105), fill="#4b5563", width=2)
        y = box[1] + 135
        for row in rows:
            centered_text(d, (box[0], y, box[2], y + 45), row, body_f)
            y += 46
    arrow(d, (390, 250), (710, 250), width=2)
    line_label(d, (525, 212), "1:N", small_f)
    arrow(d, (865, 480), (730, 650), width=2)
    line_label(d, (790, 560), "N:1", small_f)
    img.save(OUT / "fig4-2_er_fixed.png", quality=95)


def dispatch_class():
    img = Image.new("RGB", (1200, 1160), "white")
    d = ImageDraw.Draw(img)
    head_f = font(30)
    body_f = font(23)
    iface = (360, 50, 840, 300)
    fixed = (70, 390, 430, 650)
    rolling = (770, 390, 1130, 680)
    result = (210, 760, 990, 1110)
    rect_box(d, iface, "<<interface>>\nDispatchStrategy\n\n+ dispatch(prediction, market)\n+ audit_constraints()", "#FAFAFA", dashed=True, fnt=head_f)
    rect_box(d, fixed, "FixedThreshold\n\n- charge_threshold\n- discharge_threshold\n\n+ dispatch()", "#F0F1FC", fnt=body_f)
    rect_box(d, rolling, "RollingOptimization\n\n- look_ahead_hours\n- cycle_cost\n- shortfall_penalty\n\n+ dispatch()", "#F0F1FC", fnt=body_f)
    rect_box(d, result, "DispatchResult\n\n- soc_trace\n- charge_kw_trace\n- discharge_kw_trace\n- revenue\n\n+ to_dataframe()\n+ validate_constraints()", "#F0F1FC", fnt=body_f)
    arrow(d, (250, 410), (455, 300), width=2)
    arrow(d, (950, 410), (745, 300), width=2)
    arrow(d, (600, 300), (600, 760), width=2)
    line_label(d, (625, 500), "produces", font(22))
    img.save(OUT / "fig4-4_dispatch_class_fixed.png", quality=95)


def distillation():
    img = Image.new("RGB", (1300, 900), "white")
    d = ImageDraw.Draw(img)
    box_f = font(32)
    label_f = font(25)
    feat = (430, 40, 870, 155)
    teacher = (465, 215, 835, 330)
    s1 = (70, 410, 520, 595)
    s2 = (780, 410, 1230, 595)
    out = (455, 670, 845, 790)
    rounded_box(d, feat, "特征工程\n(窗口特征、价格、SOC)", "#F0F1FC", fnt=box_f)
    rounded_box(d, teacher, "教师策略\n(滚动优化调度)", "#FFFBE4", fnt=box_f)
    rounded_box(d, s1, "学生网络 Stage 1\n动作类型分类\n(charge/idle/discharge)", "#E2F7E2", fnt=box_f)
    rounded_box(d, s2, "学生网络 Stage 2\n功率预测\n(回归/分桶)", "#E2F7E2", fnt=box_f)
    rounded_box(d, out, "最终调度动作\n(动作类型 + 功率)", "#F0F1FC", fnt=box_f)
    arrow(d, (650, 155), (650, 215), width=2)
    arrow(d, (560, 330), (320, 400), width=2)
    arrow(d, (740, 330), (980, 400), width=2)
    arrow(d, (570, 155), (300, 400), width=2)
    arrow(d, (730, 155), (1000, 400), width=2)
    arrow(d, (300, 585), (560, 670), width=2)
    arrow(d, (1000, 585), (740, 670), width=2)
    line_label(d, (358, 350), "标签生成", label_f)
    line_label(d, (842, 350), "标签生成", label_f)
    # Dotted phase boxes are drawn after the solid rectangles with small white
    # gaps so their annotation does not collide with student-network text.
    for rect in [(105, 385, 485, 645), (815, 385, 1195, 645)]:
        x1, y1, x2, y2 = rect
        for x in range(x1, x2, 18):
            d.line((x, y1, min(x + 8, x2), y1), fill="#6b7280", width=2)
            d.line((x, y2, min(x + 8, x2), y2), fill="#6b7280", width=2)
        for y in range(y1, y2, 18):
            d.line((x1, y, x1, min(y + 8, y2)), fill="#6b7280", width=2)
            d.line((x2, y, x2, min(y + 8, y2)), fill="#6b7280", width=2)
    centered_text(d, (105, 645, 485, 700), "阶段一：粗分类", label_f)
    centered_text(d, (815, 645, 1195, 700), "阶段二：精细化", label_f)
    img.save(OUT / "fig4-5_distillation_fixed.png", quality=95)


if __name__ == "__main__":
    architecture()
    er()
    dispatch_class()
    distillation()
    print(f"Generated fixed figures in {OUT}")
