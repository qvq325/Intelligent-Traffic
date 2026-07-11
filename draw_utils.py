"""
绘制工具模块
在视频帧上绘制检测框、车牌信息、白名单状态等标注

支持中文文本渲染（通过 Pillow 实现）
"""
import cv2
import numpy as np
from typing import List, Tuple, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ============================================================
# 颜色定义 (BGR)
# ============================================================
COLOR_GREEN = (0, 255, 0)         # 白名单车辆 - 边框
COLOR_RED = (0, 0, 255)           # 告警
COLOR_BLUE = (255, 128, 0)        # 检测到但未识别车牌
COLOR_YELLOW = (0, 255, 255)      # 识别到车牌但非白名单
COLOR_ORANGE = (0, 165, 255)      # 非白名单车辆
COLOR_WHITE = (255, 255, 255)     # 文字
COLOR_BLACK = (0, 0, 0)           # 文字背景
COLOR_CYAN = (255, 255, 0)        # 高亮


# ============================================================
# 车辆检测框绘制
# ============================================================
def draw_vehicle_box(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
    label: str = "",
    color: Tuple[int, int, int] = COLOR_BLUE,
    thickness: int = 2,
    show_confidence: bool = True,
):
    """
    在帧上绘制车辆检测框

    Args:
        frame: 目标帧
        bbox: 边界框 (x1, y1, x2, y2)
        label: 标签文字（如 "car 0.85"）
        color: 边框颜色 (B, G, R)
        thickness: 线条粗细
        show_confidence: 是否显示置信度
    """
    x1, y1, x2, y2 = bbox
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

    if label:
        # 文字背景
        (tw, th), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
        )
        # 确保标签在画面内
        label_y = y1 - 5
        if label_y - th < 0:
            label_y = y2 + th + 5

        cv2.rectangle(
            frame,
            (x1, label_y - th - 4),
            (x1 + tw + 4, label_y),
            color,
            -1,  # 填充
        )
        cv2.putText(
            frame,
            label,
            (x1 + 2, label_y - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            COLOR_WHITE,
            1,
            cv2.LINE_AA,
        )


# ============================================================
# 车牌信息绘制（支持中文）
# ============================================================
def draw_plate_info(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
    plate_text: str,
    confidence: float,
    whitelisted: bool,
    font_size: int = 18,
):
    """
    在车辆框上方绘制车牌信息

    Args:
        frame: 目标帧
        bbox: 车辆边界框 (x1, y1, x2, y2)
        plate_text: 车牌号码
        confidence: 识别置信度
        whitelisted: 是否在白名单中
    """
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]

    # 构建信息文本
    if whitelisted:
        status = "✓ 白名单"
        bg_color = COLOR_GREEN
    else:
        status = "✗ 非白名单"
        bg_color = COLOR_ORANGE

    text = f"{plate_text} ({confidence:.0%}) {status}"

    if HAS_PIL:
        _draw_text_pil(frame, text, (x1, y1 - 30), bg_color, font_size)
    else:
        # 回退：使用 OpenCV putText（可能不支持中文）
        # 将文本拆分为多行显示
        lines = [
            f"{plate_text} ({confidence:.0%})",
            status,
        ]
        line_h = 18
        for i, line in enumerate(lines):
            ty = y1 - 8 - (len(lines) - 1 - i) * line_h
            if ty < line_h:
                ty = y2 + 8 + i * line_h
            # 文字背景
            (tw, th), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(
                frame,
                (x1, ty - th - 2),
                (x1 + tw + 4, ty + 2),
                bg_color,
                -1,
            )
            cv2.putText(
                frame,
                line,
                (x1 + 2, ty),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                COLOR_WHITE,
                1,
                cv2.LINE_AA,
            )


# ============================================================
# 仪表盘/信息面板绘制
# ============================================================
def draw_info_panel(
    frame: np.ndarray,
    lines: List[str],
    position: Tuple[int, int] = (10, 30),
    font_size: int = 16,
    alpha: float = 0.6,
    max_width: int = 350,
):
    """
    在帧上绘制半透明信息面板

    Args:
        frame: 目标帧
        lines: 文本行列表
        position: 面板左上角 (x, y)
        font_size: 字号
        alpha: 背景透明度 (0-1)
        max_width: 面板最大宽度
    """
    if not lines:
        return

    h, w = frame.shape[:2]
    line_h = font_size + 8
    panel_h = len(lines) * line_h + 16
    panel_w = max_width

    x, y = position

    # 创建半透明覆盖层
    overlay = frame.copy()
    cv2.rectangle(
        overlay,
        (x, y),
        (x + panel_w, y + panel_h),
        (30, 30, 30),
        -1,
    )
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

    # 绘制标题分隔线
    cv2.line(
        frame,
        (x + 8, y + line_h + 4),
        (x + panel_w - 8, y + line_h + 4),
        (80, 80, 80),
        1,
    )

    for i, line in enumerate(lines):
        ty = y + (i + 1) * line_h
        if HAS_PIL:
            _draw_text_pil(
                frame, line, (x + 10, ty - font_size + 2),
                (30, 30, 30), font_size,
            )
        else:
            cv2.putText(
                frame,
                line,
                (x + 10, ty),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                COLOR_WHITE,
                1,
                cv2.LINE_AA,
            )


# ============================================================
# 内部辅助: Pillow 中文文本渲染
# ============================================================
def _draw_text_pil(
    frame: np.ndarray,
    text: str,
    position: Tuple[int, int],
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    font_size: int = 18,
    text_color: Tuple[int, int, int] = (255, 255, 255),
    padding: int = 4,
):
    """
    使用 Pillow 在 OpenCV 图像上绘制中文文本

    Args:
        frame: OpenCV BGR 图像
        text: 文本内容（支持中文）
        position: (x, y) 左上角位置
        bg_color: 背景颜色 (B, G, R)
        font_size: 字号
        text_color: 文字颜色 (B, G, R)
        padding: 文字周围的内边距
    """
    if not HAS_PIL:
        return

    h, w = frame.shape[:2]

    # 尝试加载中文字体
    font = _get_cjk_font(font_size)

    # BGR -> RGB (Pillow uses RGB)
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(frame_rgb)
    draw = ImageDraw.Draw(pil_img)

    # 测量文本大小
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    x, y = position
    # 确保不超出画面
    if y < 0:
        y = 0
    if x + tw + padding * 2 > w:
        x = w - tw - padding * 2 - 4
    if x < 0:
        x = 0

    # 绘制背景
    bg_rgb = (bg_color[2], bg_color[1], bg_color[0])  # BGR -> RGB
    draw.rectangle(
        [x, y, x + tw + padding * 2, y + th + padding],
        fill=bg_rgb,
    )

    # 绘制文字
    text_rgb = (text_color[2], text_color[1], text_color[0])  # BGR -> RGB
    draw.text((x + padding, y), text, font=font, fill=text_rgb)

    # RGB -> BGR 写回
    frame_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    np.copyto(frame, frame_bgr)


# ============================================================
# 字体查找
# ============================================================
_CJK_FONT_CACHE = None


def _get_cjk_font(size: int = 18):
    """
    获取系统中可用的 CJK 字体

    优先级:
    1. Windows: Microsoft YaHei, SimHei, SimSun
    2. Linux: Noto Sans CJK, WenQuanYi, Droid Sans Fallback
    3. macOS: PingFang, Heiti SC, STHeiti
    """
    global _CJK_FONT_CACHE

    if _CJK_FONT_CACHE is not None:
        try:
            return _CJK_FONT_CACHE.font_variant(size=size)
        except Exception:
            pass

    font_paths = [
        # Windows
        "C:/Windows/Fonts/msyh.ttc",        # Microsoft YaHei
        "C:/Windows/Fonts/simhei.ttf",       # SimHei
        "C:/Windows/Fonts/simsun.ttc",       # SimSun
        "C:/Windows/Fonts/msyhbd.ttc",       # Microsoft YaHei Bold
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
    ]

    for path in font_paths:
        try:
            font = ImageFont.truetype(path, size)
            _CJK_FONT_CACHE = font
            return font
        except (IOError, OSError):
            continue

    # 回退: 使用默认字体（可能不支持中文）
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    _CJK_FONT_CACHE = font
    return font
