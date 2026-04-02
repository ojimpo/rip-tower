"""Generate PWA icons for Rip Tower."""

from PIL import Image, ImageDraw, ImageFont
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "frontend", "public", "icons")
BG_COLOR = (15, 15, 26)  # #0f0f1a
ACCENT_START = (233, 69, 96)  # #e94560
ACCENT_END = (147, 51, 234)  # purple-600 approx


def lerp_color(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def generate_icon(size: int, path: str) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rectangle background
    margin = size // 16
    radius = size // 5
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=BG_COLOR,
    )

    # Gradient rounded rectangle (inner)
    inner_margin = size // 6
    inner_radius = size // 7
    # Draw gradient by horizontal lines
    x0, y0 = inner_margin, inner_margin
    x1, y1 = size - inner_margin, size - inner_margin
    gradient_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient_img)
    for y in range(y0, y1):
        t = (y - y0) / (y1 - y0)
        color = lerp_color(ACCENT_START, ACCENT_END, t)
        gradient_draw.line([(x0, y), (x1, y)], fill=color + (255,))
    # Mask to rounded rectangle
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [x0, y0, x1, y1], radius=inner_radius, fill=255
    )
    gradient_img.putalpha(mask)
    img = Image.alpha_composite(img, gradient_img)
    draw = ImageDraw.Draw(img)

    # Letter "R"
    font_size = size // 2
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
