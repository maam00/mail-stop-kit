"""Local content PDF; no provider, web assets, HTML, or hidden attachments."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from .policy import InputError


def render_letter(request, from_address):
    try:
        import reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    except ImportError:
        raise InputError('PDF support needs the optional extra: pip install ".[lob]".') from None
    font_name = "MailStopVera"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    font = pdfmetrics.getFont(font_name)
    lines = [request["subject"], request["body"], "From: " + from_address["name"]]
    if request["kind"] == "household_kit":
        lines += [request["address"], *request["kit_residents"], *request["kit_former_residents"]]
    if any(ord(c) not in font.face.charToGlyph for c in "".join(lines) if not c.isspace()):
        raise InputError("The bundled PDF font does not support a character in this draft. Do not silently replace names; use a separately reviewed printing route.")
    if request["kind"] == "household_kit":
        return render_household_kit(request, font_name)
    data = BytesIO()
    page_count = [0]

    def page(canvas, doc):
        page_count[0] += 1
        canvas.setFont(font_name, 8)
        canvas.drawRightString(558, 36, str(doc.page))

    style = ParagraphStyle("letter", fontName=font_name, fontSize=11, leading=16, spaceAfter=10)
    title = ParagraphStyle("subject", parent=style, fontSize=13, leading=18, spaceAfter=18)
    story = [Paragraph(escape(request["subject"]), title)]
    for block in request["body"].split("\n\n"):
        story.append(Paragraph(escape(block).replace("\n", "<br/>"), style))
    story += [Spacer(1, 12), Paragraph(escape("From: " + from_address["name"]), style)]
    doc = SimpleDocTemplate(data, pagesize=(612, 792), leftMargin=54, rightMargin=54,
                           topMargin=54, bottomMargin=54, title="Postal correction request",
                           author="", creator="Mail Stop Kit", invariant=1)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    if not 1 <= page_count[0] <= 5:
        raise InputError("This adapter supports one to five content pages only; review a longer letter separately.")
    return data.getvalue(), page_count[0]


def render_household_kit(request, font_name):
    """One ordinary letter sheet with two cutouts; never an official USPS form."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    data = BytesIO()
    sheet = canvas.Canvas(data, pagesize=(612, 792), invariant=1)
    sheet.setTitle("Household mailbox paper notes")
    sheet.setAuthor("")
    sheet.setCreator("Mail Stop Kit")
    sheet.setFont(font_name, 8)
    sheet.drawString(54, 752, "Cut along the dashed guides. Ordinary letter paper, not cardstock or official USPS forms.")
    style = ParagraphStyle("kit", fontName=font_name, fontSize=10.5, leading=14, spaceAfter=8)
    heading = ParagraphStyle("kit-heading", parent=style, fontSize=13, leading=17, spaceAfter=10)
    small = ParagraphStyle("kit-small", parent=style, fontSize=9, leading=12, spaceAfter=6)
    current = "\n".join(request["kit_residents"])
    former = "\n".join(request["kit_former_residents"])
    address = request["address"]
    stamp = "Household note. Updated " + request["kit_updated_on"]
    upper = [("MAILBOX NAME GUIDE", heading), (address, style), ("CURRENT MAIL RECIPIENTS\n" + current, style),
             ("Correctly addressed Current Resident or Occupant mail remains valid.", style),
             (stamp + "\nKeep inside the box or its name slot without covering the address, lock or opening.", small)]
    if former:
        correction = ("I am a current resident at this address. Please help review repeated delivery of mail addressed only to these confirmed former residents:\n" + former)
    else:
        correction = "I am a current resident at this address. No former residents are identified in this note; please use the current-recipient information below as a name guide."
    lower = [("CARRIER COPY", heading), (address + "\n" + request["kit_updated_on"], style),
             (correction, style), ("Current mail recipients:\n" + current, style),
             ("Correctly addressed Current Resident or Occupant mail remains valid. Thank you, " + request["recipient"], style),
             ("Household note; not a USPS form or a change-of-address request. Give this copy to your carrier, or ask their preferred handoff method.", small)]
    for y, height, blocks in ((422, 304, upper), (52, 344, lower)):
        paragraphs = []
        total = 0
        for value, paragraph_style in blocks:
            paragraph = Paragraph(escape(value).replace("\n", "<br/>"), paragraph_style)
            _, h = paragraph.wrap(468, 1000)
            paragraphs.append((paragraph, h, paragraph_style.spaceAfter))
            total += h + paragraph_style.spaceAfter
        if total > height - 32:
            raise InputError("The household names or notes do not fit legibly on two paper cutouts. Nothing was clipped or reduced; use a separately reviewed larger layout.")
        sheet.setDash(3, 3)
        sheet.rect(54, y, 504, height)
        sheet.setDash()
        cursor = y + height - 16
        for paragraph, h, gap in paragraphs:
            cursor -= h
            paragraph.drawOn(sheet, 72, cursor)
            cursor -= gap
    sheet.save()
    return data.getvalue(), 1
