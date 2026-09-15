"""生成 NSIS 安装器位图（header.bmp 150x57 / sidebar.bmp 164x314）。

NSIS MUI2 要求 BMP：24 位、无压缩，尺寸即上述标准值。
品牌色取自 DESIGN-cursor.md 深色主题 token。
用法：python gen-images.py（在 nsis/ 目录下运行）
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (20, 20, 19)  # #141413 background（深色）
FG = (234, 232, 227)  # #eae8e3 on-background
ACCENT = (255, 138, 92)  # #ff8a5c primary（深色主题）

HERE = Path(__file__).parent
ICON = HERE.parent / "icons" / "icon.png"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT_ZH = "C:/Windows/Fonts/msyh.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def centered(draw: ImageDraw.ImageDraw, text: str, y: int, f: ImageFont.FreeTypeFont,
             fill, width: int) -> None:
    w = draw.textlength(text, font=f)
    draw.text(((width - w) / 2, y), text, font=f, fill=fill)


def make_header() -> None:
    img = Image.new("RGB", (150, 57), BG)
    d = ImageDraw.Draw(img)
    mark = Image.open(ICON).convert("RGBA").resize((30, 30), Image.LANCZOS)
    img.paste(mark, (8, 14), mark)
    d.text((46, 20), "Aiming Cookie", font=font(FONT_BOLD, 13), fill=FG)
    img.save(HERE / "header.bmp")


def make_sidebar() -> None:
    img = Image.new("RGB", (164, 314), BG)
    d = ImageDraw.Draw(img)
    mark = Image.open(ICON).convert("RGBA").resize((92, 92), Image.LANCZOS)
    img.paste(mark, ((164 - 92) // 2, 52), mark)
    centered(d, "Aiming Cookie", 172, font(FONT_BOLD, 15), FG, 164)
    centered(d, "AI 瞄准教练", 200, font(FONT_ZH, 12), ACCENT, 164)
    img.save(HERE / "sidebar.bmp")


if __name__ == "__main__":
    make_header()
    make_sidebar()
    print("written:", HERE / "header.bmp", HERE / "sidebar.bmp")
