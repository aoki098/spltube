"""SNSにURLを貼った時に出るカード画像(OGP画像)を作るスクリプト。

サイトのスクリーンショットは動画のサムネイルや配信者の顔が写り込むので、
自分で用意した素材だけでバナーを作っている。

    python make_ogp.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# サイトと同じ配色
BG = (251, 251, 247)
ORANGE = (255, 138, 30)
GREEN = (31, 174, 122)
TEXT = (34, 38, 43)

# OGP画像の標準サイズ
WIDTH = 1200
HEIGHT = 630
OUTPUT = Path(__file__).resolve().parent / "static" / "streams" / "ogp.png"

TITLE = "splive"
SUBTITLE = "スプラトゥーンの配信・新着動画まとめ"

LATIN_FONTS = [
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
JAPANESE_FONTS = [
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
]


def load_font(candidates, size):
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_centered(draw, text, font, center_y, fill):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x = (WIDTH - (right - left)) / 2 - left
    y = center_y - (bottom - top) / 2 - top
    draw.text((x, y), text, font=font, fill=fill)


banner = Image.new("RGB", (WIDTH, HEIGHT), BG)
draw = ImageDraw.Draw(banner, "RGBA")

# 背景の水玉模様(サイトの背景と同じ雰囲気にする)
for row_index, y in enumerate(range(0, HEIGHT + 90, 90)):
    for x in range(0, WIDTH + 90, 90):
        # 一段ごとに横へずらして、market的な均一さを崩す
        cx = x + (45 if row_index % 2 else 0)
        draw.ellipse([cx - 11, y - 11, cx + 11, y + 11], fill=GREEN + (48,))
        draw.ellipse([cx + 24, y + 33, cx + 32, y + 41], fill=ORANGE + (56,))

# 中央の白いカード(文字を読みやすくするための下地)
card = [90, 150, WIDTH - 90, HEIGHT - 150]
draw.rounded_rectangle(
    [card[0] - 5, card[1] - 5, card[2] + 5, card[3] + 5], radius=37, fill=ORANGE
)
draw.rounded_rectangle(card, radius=32, fill=(255, 255, 255))

draw_centered(draw, TITLE, load_font(LATIN_FONTS, 150), 290, GREEN)
draw_centered(draw, SUBTITLE, load_font(JAPANESE_FONTS, 44), 400, TEXT)

# 下に配色のアクセント線を引く
draw.rounded_rectangle([440, 447, WIDTH - 440, 457], radius=5, fill=ORANGE)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
banner.save(OUTPUT)
print(f"作成しました: {OUTPUT} ({WIDTH}x{HEIGHT})")
