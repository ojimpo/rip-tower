"""Generate PWA icons for Rip Tower."""

from PIL import Image, ImageDraw, ImageFont
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "frontend", "public", "icons")
ACCENT_START = (233, 69, 96)  # #e94560
ACCENT_END = (147, 51, 234)  # purple-600 approx


def lerp_color(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def generate_icon(size: int, path: str) -> None:
    # Full-bleed gradient background (no margin, no rounded rect — iOS masks it)
    img = Image.new("RGBA", (size, size), ACCENT_START)
    draw = ImageDraw.Draw(img)

    # Vertical gradient fill
    for y in range(size):
        t = y / size
        color = lerp_color(ACCENT_START, ACCENT_END, t)
        draw.line([(0, y), (size, y)], fill=color + (255,))

    draw = ImageDraw.Draw(img)

    # Letter "R"
    font_size = int(size * 0.55)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "R", font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), "R", fill=(255, 255, 255, 255), font=font)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    print(f"Generated {path} ({size}x{size})")


if __name__ == "__main__":
    generate_icon(192, os.path.join(OUTPUT_DIR, "icon-192.png"))
    generate_icon(512, os.path.join(OUTPUT_DIR, "icon-512.png"))
