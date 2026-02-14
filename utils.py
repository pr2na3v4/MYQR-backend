import segno
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
import os
import tempfile
import shutil
import uuid
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QRDesigner:
    """Handles professional QR code design with liquid/dot styling"""
    
    def __init__(self, temp_dir):
        self.temp_dir = temp_dir
        
    def create_styled_qr(self, upi_link: str, primary_color: str, 
                        logo_path: str = None) -> str:
        try:
            qr = segno.make(upi_link, error='h')
            qr_raw_path = os.path.join(self.temp_dir, f"raw_{uuid.uuid4().hex}.png")
            qr.save(qr_raw_path, scale=20, border=0)
            
            raw_img = Image.open(qr_raw_path).convert("L")
            width, height = raw_img.size
            
            upscale = 4
            canvas_size = width * upscale
            styled_qr = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 0))
            draw = ImageDraw.Draw(styled_qr)
            
            # Dot styling logic
            for y in range(0, height, 20):
                for x in range(0, width, 20):
                    if raw_img.getpixel((x + 10, y + 10)) < 128:
                        box = [x * upscale, y * upscale, (x + 20) * upscale, (y + 20) * upscale]
                        draw.ellipse(box, fill=primary_color)

            if logo_path and os.path.exists(logo_path):
                styled_qr = self._add_logo_overlay(styled_qr, logo_path)
            
            final_qr = self._add_final_styling(styled_qr)
            output_path = os.path.join(self.temp_dir, f"qr_final_{uuid.uuid4().hex}.png")
            final_qr.save(output_path, "PNG")
            return output_path
            
        except Exception as e:
            logger.error(f"Error creating professional QR: {e}")
            raise

    def _add_logo_overlay(self, qr_img: Image.Image, logo_path: str) -> Image.Image:
        logo = Image.open(logo_path).convert("RGBA")
        qr_w, qr_h = qr_img.size
        logo_size = int(qr_w * 0.20)
        logo = ImageOps.fit(logo, (logo_size, logo_size), Image.Resampling.LANCZOS)
        
        border_size = logo_size + int(qr_w * 0.04)
        white_circle = Image.new("RGBA", (border_size, border_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(white_circle)
        draw.ellipse([0, 0, border_size, border_size], fill="white")
        
        qr_img.paste(white_circle, ((qr_w - border_size)//2, (qr_h - border_size)//2), white_circle)
        qr_img.paste(logo, ((qr_w - logo_size)//2, (qr_h - logo_size)//2), logo)
        return qr_img

    def _add_final_styling(self, qr_img: Image.Image) -> Image.Image:
        padding = 100
        w, h = qr_img.size
        full_size = w + padding * 2
        container = Image.new("RGBA", (full_size, full_size), (255, 255, 255, 255))
        mask = Image.new("L", (full_size, full_size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, full_size, full_size], radius=60, fill=255)
        container.putalpha(mask)
        container.paste(qr_img, (padding, padding), qr_img)
        return container

class PosterDesigner:
    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="myqr_")
        self.qr_designer = QRDesigner(self.temp_dir)

    def generate_poster(self, shop_name: str, upi_id: str, tagline: str,
                        primary_color: str, text_color: str,
                        instagram: str = "", website: str = "", # New Logic Added here
                        logo_path: str = None) -> str:
        try:
            upi_link = f"upi://pay?pa={upi_id}&pn={shop_name.replace(' ', '%20')}"
            qr_img_path = self.qr_designer.create_styled_qr(upi_link, primary_color, logo_path)
            pdf_path = os.path.join(self.temp_dir, f"poster_{uuid.uuid4().hex}.pdf")
            
            # Pass new fields to internal PDF creator
            self._create_pdf(pdf_path, shop_name, upi_id, tagline, primary_color, text_color, qr_img_path, instagram, website)
            return pdf_path
        except Exception as e:
            logger.error(f"Poster Generation Error: {e}")
            raise

    def _create_pdf(self, pdf_path, shop_name, upi_id, tagline, primary_color, text_color, qr_img_path, instagram, website):
        c = canvas.Canvas(pdf_path, pagesize=A4)
        width, height = A4

        # 1. Background
        c.setFillColor(HexColor("#F8F9FA"))
        c.rect(0, 0, width, height, fill=1, stroke=0)

        # 2. Top Header
        header_height = 2.4 * inch
        c.setFillColor(HexColor(primary_color))
        c.rect(0, height - header_height, width, header_height, fill=1, stroke=0)

        # 3. Shop Name & Tagline
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 36)
        c.drawCentredString(width/2, height - 1.0*inch, shop_name.upper())
        
        if tagline:
            c.setFont("Helvetica", 16)
            c.drawCentredString(width/2, height - 1.4*inch, tagline)

        # 4. QR Code Card
        qr_display_size = 4.4 * inch
        qr_x = (width - qr_display_size) / 2
        qr_y = (height / 2) - (qr_display_size / 2) + 0.5*inch 

        # Shadow
        c.setFillColor(HexColor("#E0E0E0"))
        c.roundRect(qr_x + 3, qr_y - 3, qr_display_size, qr_display_size, 20, fill=1, stroke=0)
        c.drawImage(qr_img_path, qr_x, qr_y, width=qr_display_size, height=qr_display_size)

        # 5. Payment Details
        details_y = qr_y - 0.6 * inch
        c.setFillColor(HexColor(text_color))
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(width/2, details_y, "SCAN TO PAY")
        c.setFont("Helvetica", 12)
        c.setFillColor(HexColor("#444444"))
        c.drawCentredString(width/2, details_y - 0.3*inch, f"UPI ID: {upi_id}")

        # 6. SMART SOCIAL SECTION (New Strategy Logic)
        social_y = details_y - 0.9 * inch
        if instagram or website:
            c.setStrokeColor(HexColor("#EEEEEE"))
            c.line(2*inch, social_y + 0.2*inch, width - 2*inch, social_y + 0.2*inch)
            
            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(HexColor(primary_color))
            
            if instagram:
                # Mocking an Insta icon with text for simplicity
                c.drawCentredString(width/2, social_y, f"  @{instagram.replace('@', '')}")
                social_y -= 0.25 * inch
            
            if website:
                c.setFont("Helvetica", 10)
                c.setFillColor(HexColor("#666666"))
                c.drawCentredString(width/2, social_y, f"🌐  {website.lower()}")

        # 7. FOOTER SECTION
        footer_base_y = 0.8 * inch
        c.setFillColor(HexColor("#666666"))
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(width/2, footer_base_y + 0.5*inch, "ACCEPTED ON ALL UPI APPS")

        # Badges
        badge_y = footer_base_y + 0.1 * inch
        center_x = width / 2
        
        def draw_badge(x, y, text, color):
            c.setFillColor(HexColor(color))
            c.roundRect(x - 35, y - 10, 70, 20, 5, fill=1, stroke=0)
            c.setFillColor(HexColor("#FFFFFF"))
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(x, y - 3, text)

        draw_badge(center_x - 90, badge_y, "GPay", "#4285F4")
        draw_badge(center_x, badge_y, "PhonePe", "#5F259F")
        draw_badge(center_x + 90, badge_y, "Paytm", "#00BAF2")

        c.save()

# Update the wrapper function to handle new args
def generate_qr_pdf(shop_name, upi_id, tagline, primary_color, text_color, instagram="", website="", logo_path=None):
    designer = PosterDesigner()
    try:
        path = designer.generate_poster(
            shop_name=shop_name, 
            upi_id=upi_id, 
            tagline=tagline, 
            primary_color=primary_color, 
            text_color=text_color, 
            instagram=instagram, 
            website=website, 
            logo_path=logo_path
        )
        return path 
    except Exception as e:
        logger.error(f"Error in generate_qr_pdf: {e}")
        raise