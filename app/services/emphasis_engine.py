import re
from pathlib import Path
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont


class EmphasisEngine:
    """
    Renders visual callout highlights and emphasis overlays for key technical/narrative actions.
    """

    def create_emphasis_overlay(
        self,
        text: str,
        emphasis_keywords: List[str],
        width: int,
        height: int,
        out_png: Path,
        scene_id: int = 1
    ) -> Optional[Path]:
        """
        Creates an RGBA overlay PNG with highlighted emphasis badge or action pill.
        """
        if not emphasis_keywords:
            return None

        out_png.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("arial.ttf", 22)
            font_bold = ImageFont.truetype("arialbd.ttf", 24)
        except Exception:
            font = ImageFont.load_default()
            font_bold = font

        # Top-right Action / Emphasis Callout Badge
        primary_term = emphasis_keywords[0]
        badge_text = f"ACTION: {primary_term.upper()}"
        bbox = draw.textbbox((0, 0), badge_text, font=font_bold)
        bw = bbox[2] - bbox[0] + 36
        bh = 46

        bx2 = width - 40
        bx1 = bx2 - bw
        by1 = 40
        by2 = by1 + bh

        # Draw glowing gradient backdrop
        draw.rounded_rectangle([(bx1 - 3, by1 - 3), (bx2 + 3, by2 + 3)], radius=14, fill=(99, 102, 241, 100))
        draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=12, fill=(15, 23, 42, 230), outline=(56, 189, 248, 255), width=2)
        draw.text((bx1 + 18, by1 + 10), badge_text, fill=(56, 189, 248, 255), font=font_bold)

        # Top-left Step Indicator (e.g. "STEP 03")
        step_text = f"STEP {scene_id:02d}"
        s_bbox = draw.textbbox((0, 0), step_text, font=font_bold)
        sw = s_bbox[2] - s_bbox[0] + 32
        sh = 42
        draw.rounded_rectangle([(40, 40), (40 + sw, 40 + sh)], radius=10, fill=(99, 102, 241, 230))
        draw.text((56, 48), step_text, fill=(255, 255, 255, 255), font=font_bold)

        img.save(out_png, "PNG")
        return out_png
