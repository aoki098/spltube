"""ホーム画面用のアイコン(PNG)を作るスクリプト。

タブのアイコンと同じデザイン(オレンジと緑で斜めに分けた角丸の四角に白いS)。
一度作れば使い回すので、デザインを変えたい時だけ実行すればよい。

    python make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ORANGE = (255, 138, 30)
GREEN = (31, 174, 122)
SIZE = 180
OUTPUT = Path(__file__).resolve().parent / "static" / "streams" / "apple-touch-icon.png"

FONT_CANDIDATES = [
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ギザギザを防ぐため4倍の大きさで描いてから縮小する
canvas_size = SIZE * 4

# 1. まず四角いまま色を塗る(オレンジの上に右下半分の緑の三角形を重ねる)
picture = Image.new("RGB", (canvas_size, canvas_size), ORANGE)
draw = ImageDraw.Draw(picture)
draw.polygon([(canvas_size, 0), (canvas_size, canvas_size), (0, canvas_size)], fill=GREEN)

# 2. 白い「S」を中央に置く
font = load_font(int(canvas_size * 0.55))
left, top, right, bottom = draw.textbbox((0, 0), "S", font=font)
draw.text(
    ((canvas_size - (right - left)) / 2 - left, (canvas_size - (bottom - top)) / 2 - top),
    "S",
    font=font,
    fill=(255, 255, 255),
)

# 3. 最後に角丸の形で切り抜く(先に塗ってから抜くので、角が欠けない)
mask = Image.new("L", (canvas_size, canvas_size), 0)
ImageDraw.Draw(mask).rounded_rectangle(
    [0, 0, canvas_size - 1, canvas_size - 1], radius=int(canvas_size * 0.22), fill=255
)

icon = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
icon.paste(picture, (0, 0), mask)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
icon.resize((SIZE, SIZE), Image.LANCZOS).save(OUTPUT)
print(f"作成しました: {OUTPUT}")
