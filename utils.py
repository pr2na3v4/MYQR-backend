import os
import uuid
import logging
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import segno
from PIL import Image, ImageDraw, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor

logger = logging.getLogger(__name__)


@dataclass
class PosterConfig:
    # Required
    shop_name:     str
    upi_id:        str
    landing_url:   str
    primary_color: str
    text_color:    str
    # Optional
    tagline:     str = ""
    instagram:   str = ""
    website_url: str = ""
    logo_path:   Optional[str] = None


class QRDesigner:
    """Handles QR code generation with custom dot styling and optional logo overlay."""

    QR_SCALE = 20
    QR_UPSCALE = 4
    QR_BORDER = 0
    LOGO_SIZE_RATIO = 0.22
    LOGO_BORDER_RATIO = 0.03

    def __init__(self, work_dir: str) -> None:
        self.work_dir = Path(work_dir)

    def create_styled_qr(self, url: str, primary_color: str, logo_path: Optional[str] = None) -> str:
        """
        Generate a styled QR code (circular dots) pointing to `url`.
        Optionally overlays a logo at the centre.

        Returns the absolute path to the final PNG file.
        """
        raw_path = self._render_raw_qr(url)
        styled = self._apply_dot_style(raw_path, primary_color)

        if logo_path and Path(logo_path).exists():
            styled = self._add_logo_overlay(styled, logo_path)

        output_path = self.work_dir / f"qr_{uuid.uuid4().hex}.png"
        styled.save(str(output_path), "PNG")
        logger.info("Styled QR saved → %s", output_path)
        return str(output_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _render_raw_qr(self, url: str) -> Path:
        """Render a high-error-correction QR to a temporary PNG and return its path."""
        qr = segno.make(url, error="h")
        raw_path = self.work_dir / f"raw_{uuid.uuid4().hex}.png"
        qr.save(str(raw_path), scale=self.QR_SCALE, border=self.QR_BORDER)
        return raw_path

    def _apply_dot_style(self, raw_path: Path, color: str) -> Image.Image:
        """Replace square QR modules with filled ellipses in `color`."""
        raw_img = Image.open(str(raw_path)).convert("L")
        w, h = raw_img.size
        canvas_size = w * self.QR_UPSCALE

        styled = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 0))
        draw = ImageDraw.Draw(styled)

        step = self.QR_SCALE  # matches the scale used when saving the raw QR
        for y in range(0, h, step):
            for x in range(0, w, step):
                sample_x = min(x + step // 2, w - 1)
                sample_y = min(y + step // 2, h - 1)
                if raw_img.getpixel((sample_x, sample_y)) < 128:
                    box = [
                        x * self.QR_UPSCALE,
                        y * self.QR_UPSCALE,
                        (x + step) * self.QR_UPSCALE,
                        (y + step) * self.QR_UPSCALE,
                    ]
                    draw.ellipse(box, fill=color)

        return styled

    def _add_logo_overlay(self, qr_img: Image.Image, logo_path: str) -> Image.Image:
        """Paste a circular-cropped logo with a white border at the centre of the QR."""
        logo = Image.open(logo_path).convert("RGBA")
        qr_w, qr_h = qr_img.size

        logo_size = int(qr_w * self.LOGO_SIZE_RATIO)
        border_size = logo_size + int(qr_w * self.LOGO_BORDER_RATIO)

        logo = ImageOps.fit(logo, (logo_size, logo_size), Image.Resampling.LANCZOS)

        # Circular mask for the white backing circle
        mask = Image.new("L", (border_size, border_size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, border_size, border_size], fill=255)

        white_circle = Image.new("RGBA", (border_size, border_size), "white")
        cx = (qr_w - border_size) // 2
        cy = (qr_h - border_size) // 2
        qr_img.paste(white_circle, (cx, cy), mask)

        lx = (qr_w - logo_size) // 2
        ly = (qr_h - logo_size) // 2
        qr_img.paste(logo, (lx, ly), logo)

        return qr_img


class PosterDesigner:
    """Composes a full A4 PDF poster: branded header, styled QR, and footer."""

    def __init__(self, work_dir: str) -> None:
        self.work_dir = Path(work_dir)
        self.qr_designer = QRDesigner(work_dir)

    def generate_poster(self, config: PosterConfig) -> str:
        """
        Build an A4 PDF poster for `config`.

        Returns the absolute path to the generated PDF.
        """
        qr_path = self.qr_designer.create_styled_qr(
            url=config.landing_url,
            primary_color=config.primary_color,
            logo_path=config.logo_path,
        )
        pdf_path = self._render_pdf(config, qr_path)
        logger.info("Poster PDF saved → %s", pdf_path)
        return pdf_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _render_pdf(self, config: PosterConfig, qr_path: str) -> str:
        pdf_path = str(self.work_dir / f"poster_{uuid.uuid4().hex}.pdf")
        c = canvas.Canvas(pdf_path, pagesize=A4)
        width, height = A4

        self._draw_background(c, width, height)
        self._draw_header(c, width, height, config)
        self._draw_qr(c, width, height, qr_path)
        self._draw_footer(c, width, config)

        c.save()
        return pdf_path

    def _draw_background(self, c, width: float, height: float) -> None:
        c.setFillColor(HexColor("#FDFDFD"))
        c.rect(0, 0, width, height, fill=1, stroke=0)

    def _draw_header(self, c, width: float, height: float, config: PosterConfig) -> None:
        header_height = 2.5 * inch

        c.setFillColor(HexColor(config.primary_color))
        c.rect(0, height - header_height, width, header_height, fill=1, stroke=0)

        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 38)
        c.drawCentredString(width / 2, height - 1.1 * inch, config.shop_name.upper())

        if config.tagline:
            c.setFont("Helvetica", 16)
            c.drawCentredString(width / 2, height - 1.5 * inch, config.tagline)

    def _draw_qr(self, c, width: float, height: float, qr_path: str) -> None:
        qr_size = 4.5 * inch
        x = (width - qr_size) / 2
        y = height / 2 - inch
        c.drawImage(qr_path, x, y, width=qr_size, height=qr_size, mask="auto")

    def _draw_footer(self, c, width: float, config: PosterConfig) -> None:
        text_color = HexColor(config.text_color or "#333333")
        c.setFillColor(text_color)

        # Primary CTA
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(width / 2, 2.5 * inch, "SCAN TO DISCOVER & PAY")

        # UPI ID
        c.setFont("Helvetica", 11)
        c.drawCentredString(width / 2, 2.2 * inch, f"UPI: {config.upi_id}")

        # Optional social / web links
        link_y = 1.95 * inch
        if config.instagram:
            c.drawCentredString(width / 2, link_y, f"Instagram: @{config.instagram.lstrip('@')}")
            link_y -= 0.22 * inch
        if config.website_url:
            c.drawCentredString(width / 2, link_y, config.website_url)
            link_y -= 0.22 * inch

        # Powered-by line
        c.setFillColor(HexColor("#999999"))
        c.setFont("Helvetica", 9)
        c.drawCentredString(width / 2, 1.4 * inch, "Powered by MYQR V2")


def cleanup_directory(path: str) -> None:
    """Remove a temporary working directory, logging any errors."""
    try:
        shutil.rmtree(path, ignore_errors=True)
        logger.debug("Cleaned up temp dir: %s", path)
    except Exception as exc:
        logger.warning("Failed to clean up %s: %s", path, exc)