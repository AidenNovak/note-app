from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from datetime import datetime
from io import BytesIO
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    Image = ImageDraw = ImageFilter = ImageFont = None  # type: ignore[assignment]

from app.schemas import InsightShareCardMetricOut, InsightShareCardOut

# Render at 2x then downscale for anti-aliasing
_SCALE = 2
CARD_WIDTH = 600 * _SCALE  # 1200px output
CARD_PADDING = 44 * _SCALE

_THEMES: dict[str, dict[str, str]] = {
    "trend": {
        "background": "#F5F3ED",
        "surface": "#FFFFFF",
        "surface_alt": "rgba(0,0,0,0.03)",
        "primary": "#1A4A3A",
        "accent": "#1A4A3A",
        "text": "#0A0A0A",
        "muted": "#555555",
        "outline": "#E5E2DB",
    },
    "connection": {
        "background": "#F5F3ED",
        "surface": "#FFFFFF",
        "surface_alt": "rgba(0,0,0,0.03)",
        "primary": "#2C3E8C",
        "accent": "#2C3E8C",
        "text": "#0A0A0A",
        "muted": "#555555",
        "outline": "#E5E2DB",
    },
    "gap": {
        "background": "#F5F3ED",
        "surface": "#FFFFFF",
        "surface_alt": "rgba(0,0,0,0.03)",
        "primary": "#8B6914",
        "accent": "#8B6914",
        "text": "#0A0A0A",
        "muted": "#555555",
        "outline": "#E5E2DB",
    },
    "opportunity": {
        "background": "#F5F3ED",
        "surface": "#FFFFFF",
        "surface_alt": "rgba(0,0,0,0.03)",
        "primary": "#5B2D8E",
        "accent": "#5B2D8E",
        "text": "#0A0A0A",
        "muted": "#555555",
        "outline": "#E5E2DB",
    },
    "report": {
        "background": "#F5F3ED",
        "surface": "#FFFFFF",
        "surface_alt": "rgba(0,0,0,0.03)",
        "primary": "#2C2C2C",
        "accent": "#2C2C2C",
        "text": "#0A0A0A",
        "muted": "#555555",
        "outline": "#E5E2DB",
    },
}


def _pick_theme(report_type: str) -> tuple[str, dict[str, str]]:
    theme_key = str(report_type or "report").strip().lower()
    if theme_key not in _THEMES:
        theme_key = "report"
    return theme_key, _THEMES[theme_key]


def _as_text(value: object, default: str = "") -> str:
    text = str(value or default).strip()
    return text


def _get_field(item: Mapping[str, Any] | object, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _percent_label(value: float) -> str:
    return f"{round(max(0.0, min(value, 1.0)) * 100)}%"


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 3].rstrip()}..."


def _normalize_metrics(raw_metrics: object, fallback_metrics: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(raw_metrics, list):
        return fallback_metrics

    metrics: list[dict[str, str]] = []
    for item in raw_metrics:
        if not isinstance(item, Mapping):
            continue
        label = _truncate(_as_text(item.get("label")), 24)
        value = _truncate(_as_text(item.get("value")), 24)
        if not label or not value:
            continue
        metrics.append({"label": label, "value": value})
    return metrics or fallback_metrics


def build_share_card_payload(
    *,
    report_type: str,
    title: str,
    description: str,
    confidence: float,
    importance_score: float,
    novelty_score: float,
    generated_at: datetime,
    review_summary: str | None = None,
    evidence_items: Sequence[Mapping[str, Any] | object] | None = None,
    action_items: Sequence[Mapping[str, Any] | object] | None = None,
    raw_share_card: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    evidence_items = evidence_items or []
    action_items = action_items or []

    first_evidence = evidence_items[0] if evidence_items else None
    first_action = action_items[0] if action_items else None
    theme_key, _palette = _pick_theme(report_type)

    fallback_metrics: list[dict[str, str]] = []
    fallback = {
        "theme": theme_key,
        "eyebrow": str(report_type or "report").upper(),
        "headline": _truncate(title or "Insight", 110),
        "summary": _truncate(description or "No summary available.", 500),
        "highlight": _truncate(_as_text(review_summary), 260) or None,
        "evidence_quote": _truncate(_as_text(_get_field(first_evidence, "quote")), 400) or None,
        "evidence_source": (
            _truncate(_as_text(_get_field(first_evidence, "note_title")), 80)
            or _truncate(_as_text(_get_field(first_evidence, "note_id")), 80)
            or None
        ),
        "action_title": _truncate(_as_text(_get_field(first_action, "title")), 160) or None,
        "action_detail": _truncate(_as_text(_get_field(first_action, "detail")), 300) or None,
        "metrics": fallback_metrics,
        "footer": "生成于 " + generated_at.strftime("%Y年%m月%d日"),
    }
    if not isinstance(raw_share_card, Mapping):
        return fallback

    return {
        "theme": _pick_theme(_as_text(raw_share_card.get("theme"), theme_key))[0],
        "eyebrow": _truncate(_as_text(raw_share_card.get("eyebrow"), fallback["eyebrow"]), 48),
        "headline": _truncate(_as_text(raw_share_card.get("headline"), fallback["headline"]), 110),
        "summary": _truncate(_as_text(raw_share_card.get("summary"), fallback["summary"]), 500),
        "highlight": _truncate(_as_text(raw_share_card.get("highlight"), fallback["highlight"] or ""), 260)
        or None,
        "evidence_quote": _truncate(
            _as_text(raw_share_card.get("evidence_quote"), fallback["evidence_quote"] or ""),
            400,
        )
        or None,
        "evidence_source": _truncate(
            _as_text(raw_share_card.get("evidence_source"), fallback["evidence_source"] or ""),
            80,
        )
        or None,
        "action_title": _truncate(
            _as_text(raw_share_card.get("action_title"), fallback["action_title"] or ""),
            160,
        )
        or None,
        "action_detail": _truncate(
            _as_text(raw_share_card.get("action_detail"), fallback["action_detail"] or ""),
            300,
        )
        or None,
        "metrics": _normalize_metrics(raw_share_card.get("metrics"), fallback_metrics),
        "footer": _truncate(_as_text(raw_share_card.get("footer"), fallback["footer"]), 72),
    }


def extract_share_card_payload(report_json: str | None) -> dict[str, Any] | None:
    if not report_json:
        return None
    try:
        parsed = json.loads(report_json)
    except json.JSONDecodeError:
        return None
    share_card = parsed.get("share_card")
    return share_card if isinstance(share_card, dict) else None


def build_share_card_model(**kwargs: Any) -> InsightShareCardOut:
    payload = build_share_card_payload(**kwargs)
    metrics = [InsightShareCardMetricOut(**metric) for metric in payload.pop("metrics")]
    return InsightShareCardOut(metrics=metrics, **payload)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    scaled = size * _SCALE
    candidates = [
        # Prefer serif CJK fonts for magazine feel
        "/System/Library/Fonts/STSong.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        # Linux CJK
        "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        # Latin fallback
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=scaled)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _apply_noise_texture(image: Image.Image, opacity: float = 0.04) -> None:
    """Add subtle noise texture for magazine/paper feel."""
    noise = Image.new("L", image.size)
    rng = random.Random(42)
    pixels = noise.load()
    # Sparse noise for performance
    for y in range(0, image.size[1], 2):
        for x in range(0, image.size[0], 2):
            val = rng.randint(0, 255)
            pixels[x, y] = val
            if x + 1 < image.size[0]:
                pixels[x + 1, y] = val
            if y + 1 < image.size[1]:
                pixels[x, y + 1] = val
                if x + 1 < image.size[0]:
                    pixels[x + 1, y + 1] = val
    noise = noise.filter(ImageFilter.GaussianBlur(radius=1))
    noise_rgba = Image.new("RGBA", image.size, (128, 128, 128, 0))
    noise_data = noise.load()
    rgba_data = noise_rgba.load()
    alpha = int(255 * opacity)
    for y in range(image.size[1]):
        for x in range(image.size[0]):
            v = noise_data[x, y]
            rgba_data[x, y] = (v, v, v, alpha)
    image_rgba = image.convert("RGBA")
    image_rgba = Image.alpha_composite(image_rgba, noise_rgba)
    image.paste(image_rgba.convert("RGB"))


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
        or 0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
    )


def _tokenize_for_wrap(text: str) -> list[str]:
    """Split text into tokens: CJK chars individually, Latin words as groups."""
    tokens: list[str] = []
    current = ""
    for char in text:
        if _is_cjk(char):
            if current:
                tokens.append(current)
                current = ""
            tokens.append(char)
        elif char == " ":
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int | None = None,
) -> list[str]:
    tokens = _tokenize_for_wrap(text)
    if not tokens:
        return []

    lines: list[str] = []
    current = tokens[0]
    for token in tokens[1:]:
        separator = "" if _is_cjk(token[0]) or (current and _is_cjk(current[-1])) else " "
        candidate = f"{current}{separator}{token}"
        width, _ = _measure_text(draw, candidate, font)
        if width <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = token
    lines.append(current)

    if max_lines is None or len(lines) <= max_lines:
        return lines

    trimmed = lines[:max_lines]
    while trimmed:
        candidate = f"{trimmed[-1].rstrip('.')}..."
        width, _ = _measure_text(draw, candidate, font)
        if width <= max_width:
            trimmed[-1] = candidate
            return trimmed
        # Remove last word/char
        last = trimmed[-1]
        if " " in last:
            trimmed[-1] = last.rsplit(" ", 1)[0]
        elif len(last) > 1:
            trimmed[-1] = last[:-1]
        else:
            break
    return lines[:max_lines]


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    *,
    x: int,
    y: int,
    max_width: int,
    fill: str,
    line_gap: int,
    max_lines: int | None = None,
) -> int:
    lines = _wrap_text(draw, text, font, max_width, max_lines=max_lines)
    current_y = y
    for line in lines:
        draw.text((x, current_y), line, font=font, fill=fill)
        _, height = _measure_text(draw, line, font)
        current_y += height + line_gap
    return current_y


def _draw_accent_bar(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, color: str) -> None:
    """Draw a horizontal accent bar (Swiss International Style)."""
    draw.rectangle((x, y, x + width, y + 6 * _SCALE), fill=color)


def _draw_bg_block(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    accent_color: str,
    bg_color: str = "#F0EDE6",
) -> None:
    """Draw a content block with left accent border (info-card style .bg-block)."""
    bg_rgb = _hex_to_rgb(bg_color)
    draw.rectangle((x, y, x + width, y + height), fill=bg_rgb)
    draw.rectangle((x, y, x + 5 * _SCALE, y + height), fill=accent_color)


def render_share_card_png(card_input: InsightShareCardOut | Mapping[str, Any]) -> bytes:
    card = (
        card_input
        if isinstance(card_input, InsightShareCardOut)
        else InsightShareCardOut.model_validate(card_input)
    )
    _theme_key, palette = _pick_theme(card.theme)

    # Font hierarchy (info-card-designer inspired)
    title_font = _load_font(56, bold=False)      # Main headline — large, no bold for elegance
    eyebrow_font = _load_font(16, bold=True)      # Category label
    body_font = _load_font(20)                     # Summary text
    quote_font = _load_font(22, bold=False)        # Evidence quote
    h3_font = _load_font(28, bold=False)           # Section headers
    small_font = _load_font(15)                    # Footer / detail

    # Dynamic height calculation — measure content first
    # Use a temp image to measure text
    tmp = Image.new("RGB", (CARD_WIDTH, 100))
    tmp_draw = ImageDraw.Draw(tmp)

    content_x = CARD_PADDING
    content_width = CARD_WIDTH - CARD_PADDING * 2
    gap = 28 * _SCALE  # Standard gap between sections

    # Calculate total height
    y = CARD_PADDING

    # Accent bar
    y += 8 * _SCALE
    y += gap

    # Eyebrow
    _, eh = _measure_text(tmp_draw, card.eyebrow, eyebrow_font)
    y += eh + 16 * _SCALE

    # Headline
    headline_lines = _wrap_text(tmp_draw, card.headline, title_font, content_width, max_lines=3)
    for line in headline_lines:
        _, lh = _measure_text(tmp_draw, line, title_font)
        y += lh + 8 * _SCALE
    y += gap

    # Summary
    summary_lines = _wrap_text(tmp_draw, card.summary, body_font, content_width, max_lines=8)
    for line in summary_lines:
        _, lh = _measure_text(tmp_draw, line, body_font)
        y += lh + 10 * _SCALE
    y += gap

    # Evidence block
    if card.evidence_quote:
        y += 24 * _SCALE  # block padding top
        _, label_h = _measure_text(tmp_draw, "\u8bc1\u636e", eyebrow_font)
        y += label_h + 12 * _SCALE
        quote_lines = _wrap_text(
            tmp_draw, f"\u201C{card.evidence_quote}\u201D", quote_font,
            content_width - 40 * _SCALE, max_lines=6,
        )
        for line in quote_lines:
            _, lh = _measure_text(tmp_draw, line, quote_font)
            y += lh + 8 * _SCALE
        if card.evidence_source:
            y += 10 * _SCALE
            _, sh = _measure_text(tmp_draw, card.evidence_source, small_font)
            y += sh
        y += 24 * _SCALE  # block padding bottom
        y += gap

    # Action block
    if card.action_title or card.action_detail:
        y += 24 * _SCALE
        _, label_h = _measure_text(tmp_draw, "\u4e0b\u4e00\u6b65", eyebrow_font)
        y += label_h + 12 * _SCALE
        if card.action_title:
            action_lines = _wrap_text(tmp_draw, card.action_title, h3_font, content_width - 40 * _SCALE, max_lines=2)
            for line in action_lines:
                _, lh = _measure_text(tmp_draw, line, h3_font)
                y += lh + 8 * _SCALE
        if card.action_detail:
            y += 8 * _SCALE
            detail_lines = _wrap_text(tmp_draw, card.action_detail, small_font, content_width - 40 * _SCALE, max_lines=4)
            for line in detail_lines:
                _, lh = _measure_text(tmp_draw, line, small_font)
                y += lh + 6 * _SCALE
        y += 24 * _SCALE
        y += gap

    # Footer
    _, fh = _measure_text(tmp_draw, card.footer, small_font)
    y += fh + 16 * _SCALE

    y += CARD_PADDING
    card_height = y

    # Create the actual image
    bg_rgb = _hex_to_rgb(palette["background"])
    image = Image.new("RGB", (CARD_WIDTH, card_height), bg_rgb)

    # Apply noise texture for paper feel
    _apply_noise_texture(image, opacity=0.035)

    draw = ImageDraw.Draw(image)

    # -- Draw content --
    cy = CARD_PADDING

    # Accent bar (Swiss style -- single short bar)
    _draw_accent_bar(draw, content_x, cy, 80 * _SCALE, palette["accent"])
    cy += 8 * _SCALE + gap

    # Eyebrow label
    draw.text(
        (content_x, cy), card.eyebrow.upper(), font=eyebrow_font,
        fill=palette["accent"],
    )
    _, eh = _measure_text(draw, card.eyebrow, eyebrow_font)
    cy += eh + 16 * _SCALE

    # Headline
    cy = _draw_wrapped_text(
        draw, card.headline, title_font,
        x=content_x, y=cy, max_width=content_width,
        fill=palette["text"], line_gap=8 * _SCALE, max_lines=3,
    )
    cy += gap

    # Summary
    cy = _draw_wrapped_text(
        draw, card.summary, body_font,
        x=content_x, y=cy, max_width=content_width,
        fill=palette["muted"], line_gap=10 * _SCALE, max_lines=8,
    )
    cy += gap

    # Evidence block (bg-block style: light bg + left accent border)
    if card.evidence_quote:
        block_start = cy
        inner_x = content_x + 20 * _SCALE
        inner_width = content_width - 40 * _SCALE
        block_cy = cy + 24 * _SCALE

        draw.text((inner_x, block_cy), "\u8bc1\u636e", font=eyebrow_font, fill=palette["accent"])
        _, label_h = _measure_text(draw, "\u8bc1\u636e", eyebrow_font)
        block_cy += label_h + 12 * _SCALE

        block_cy = _draw_wrapped_text(
            draw, f"\u201C{card.evidence_quote}\u201D", quote_font,
            x=inner_x, y=block_cy, max_width=inner_width,
            fill=palette["text"], line_gap=8 * _SCALE, max_lines=6,
        )

        if card.evidence_source:
            block_cy += 10 * _SCALE
            draw.text((inner_x, block_cy), f"\u2014 {card.evidence_source}", font=small_font, fill=palette["muted"])
            _, sh = _measure_text(draw, card.evidence_source, small_font)
            block_cy += sh

        block_cy += 24 * _SCALE
        block_height = block_cy - block_start

        # Draw bg block behind (we draw it on a separate layer and composite)
        block_bg = Image.new("RGBA", (content_width, block_height), (0, 0, 0, int(255 * 0.03)))
        image_rgba = image.convert("RGBA")
        image_rgba.paste(block_bg, (content_x, block_start), block_bg)
        image.paste(image_rgba.convert("RGB"))
        # Redraw the accent left border on top
        draw = ImageDraw.Draw(image)
        accent_rgb = _hex_to_rgb(palette["accent"])
        draw.rectangle(
            (content_x, block_start, content_x + 5 * _SCALE, block_start + block_height),
            fill=accent_rgb,
        )

        cy = block_cy + gap

    # Action block (primary color bg, white text)
    if card.action_title or card.action_detail:
        block_start = cy
        inner_x = content_x + 20 * _SCALE
        inner_width = content_width - 40 * _SCALE
        block_cy = cy + 24 * _SCALE

        # Measure block height first
        measure_cy = block_cy
        _, label_h = _measure_text(draw, "\u4e0b\u4e00\u6b65", eyebrow_font)
        measure_cy += label_h + 12 * _SCALE
        if card.action_title:
            for line in _wrap_text(draw, card.action_title, h3_font, inner_width, max_lines=2):
                _, lh = _measure_text(draw, line, h3_font)
                measure_cy += lh + 8 * _SCALE
        if card.action_detail:
            measure_cy += 8 * _SCALE
            for line in _wrap_text(draw, card.action_detail, small_font, inner_width, max_lines=4):
                _, lh = _measure_text(draw, line, small_font)
                measure_cy += lh + 6 * _SCALE
        measure_cy += 24 * _SCALE
        block_height = measure_cy - block_start

        # Draw rounded rect background
        draw.rounded_rectangle(
            (content_x, block_start, content_x + content_width, block_start + block_height),
            radius=16 * _SCALE,
            fill=palette["primary"],
        )

        draw.text((inner_x, block_cy), "\u4e0b\u4e00\u6b65", font=eyebrow_font, fill="#FFFFFF")
        block_cy += label_h + 12 * _SCALE

        if card.action_title:
            block_cy = _draw_wrapped_text(
                draw, card.action_title, h3_font,
                x=inner_x, y=block_cy, max_width=inner_width,
                fill="#FFFFFF", line_gap=8 * _SCALE, max_lines=2,
            )
        if card.action_detail:
            block_cy += 8 * _SCALE
            block_cy = _draw_wrapped_text(
                draw, card.action_detail, small_font,
                x=inner_x, y=block_cy, max_width=inner_width,
                fill="#E8E4DD", line_gap=6 * _SCALE, max_lines=4,
            )

        cy = block_start + block_height + gap

    # Footer -- separator + right-aligned text
    separator_y = cy
    draw.line(
        (content_x, separator_y, content_x + content_width, separator_y),
        fill=_hex_to_rgb("#D5D2CB"), width=1,
    )
    cy += 16 * _SCALE
    footer_width, footer_height = _measure_text(draw, card.footer, small_font)
    draw.text(
        (content_x + content_width - footer_width, cy),
        card.footer, font=small_font, fill=palette["muted"],
    )

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
