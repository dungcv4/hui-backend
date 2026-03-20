"""
PDF Bill Generator for HuiManager
Generates professional PDF invoices with QR codes
"""
import io
import os
import qrcode
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import urllib.parse
import logging

logger = logging.getLogger(__name__)

# Register Vietnamese-compatible fonts
FONTS_DIR = os.path.join(os.path.dirname(__file__), 'fonts')
try:
    pdfmetrics.registerFont(TTFont('NotoSans', os.path.join(FONTS_DIR, 'NotoSans-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('NotoSans-Bold', os.path.join(FONTS_DIR, 'NotoSans-Bold.ttf')))
    FONT_NAME = 'NotoSans'
    FONT_BOLD = 'NotoSans-Bold'
    logger.info("Loaded NotoSans fonts for Vietnamese support")
except Exception as e:
    logger.warning(f"Could not load NotoSans fonts: {e}. Using default fonts.")
    FONT_NAME = 'Helvetica'
    FONT_BOLD = 'Helvetica-Bold'


def generate_qr_code_image(data: str, size: int = 150) -> io.BytesIO:
    """Generate QR code as image bytes"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to bytes
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    return img_bytes


def generate_vietqr_url(
    bank_code: str,
    account_number: str,
    amount: float,
    content: str,
    account_name: str = None
) -> str:
    """Generate VietQR URL"""
    bank_codes = {
        'Vietcombank': 'VCB', 'Techcombank': 'TCB', 'MB Bank': 'MB', 'MB': 'MB',
        'BIDV': 'BIDV', 'Agribank': 'VBA', 'VPBank': 'VPB', 'ACB': 'ACB',
        'Sacombank': 'STB', 'TPBank': 'TPB', 'VIB': 'VIB',
    }
    
    code = bank_codes.get(bank_code, bank_code)
    encoded_content = urllib.parse.quote(content)
    encoded_name = urllib.parse.quote(account_name or '')
    
    return f"https://img.vietqr.io/image/{code}-{account_number}-compact2.png?amount={int(amount)}&addInfo={encoded_content}&accountName={encoded_name}"


def format_currency(amount: float) -> str:
    """Format amount as Vietnamese currency"""
    return f"{amount:,.0f}".replace(",", ".") + " đ"


def generate_bill_pdf(
    member_name: str,
    member_phone: str,
    hui_group_name: str,
    cycle_number: int,
    total_cycles: int,
    amount: float,
    due_date: datetime,
    payment_code: str,
    bank_name: str = None,
    bank_account_number: str = None,
    bank_account_name: str = None,
    slot_count: int = 1,
    owner_name: str = None,
    owner_phone: str = None
) -> io.BytesIO:
    """Generate PDF bill with QR code"""
    
    buffer = io.BytesIO()
    
    # Create document
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )
    
    # Styles with Vietnamese font
    styles = getSampleStyleSheet()
    
    # Custom styles with NotoSans font
    title_style = ParagraphStyle(
        'Title',
        fontName=FONT_BOLD,
        fontSize=20,
        alignment=TA_CENTER,
        spaceAfter=10,
        textColor=colors.HexColor('#1e40af')
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        fontName=FONT_NAME,
        fontSize=12,
        alignment=TA_CENTER,
        spaceAfter=20,
        textColor=colors.HexColor('#64748b')
    )
    
    section_title = ParagraphStyle(
        'SectionTitle',
        fontName=FONT_BOLD,
        fontSize=14,
        spaceAfter=10,
        textColor=colors.HexColor('#0f172a'),
        borderWidth=0,
        borderColor=colors.HexColor('#e2e8f0'),
        borderPadding=5
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        fontName=FONT_NAME,
        fontSize=11,
        leading=16
    )
    
    # Build content
    story = []
    
    # Header
    story.append(Paragraph("HÓA ĐƠN ĐÓNG HỤI", title_style))
    story.append(Paragraph(f"HuiManager Pro - {datetime.now().strftime('%d/%m/%Y %H:%M')}", subtitle_style))
    story.append(Spacer(1, 10))
    
    # Hui Group Info
    story.append(Paragraph("THÔNG TIN DÂY HỤI", section_title))
    
    hui_data = [
        ["Tên dây hụi:", hui_group_name],
        ["Kỳ thanh toán:", f"{cycle_number} / {total_cycles}"],
        ["Hạn đóng:", due_date.strftime("%d/%m/%Y") if due_date else "N/A"],
    ]
    
    hui_table = Table(hui_data, colWidths=[5*cm, 12*cm])
    hui_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
        ('FONTNAME', (1, 0), (1, -1), FONT_BOLD),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(hui_table)
    story.append(Spacer(1, 15))
    
    # Member Info
    story.append(Paragraph("THÔNG TIN THÀNH VIÊN", section_title))
    
    slot_info = f" ({slot_count} chân)" if slot_count > 1 else ""
    member_data = [
        ["Họ tên:", f"{member_name}{slot_info}"],
        ["Số điện thoại:", member_phone or "N/A"],
        ["Mã thanh toán:", payment_code or "N/A"],
    ]
    
    member_table = Table(member_data, colWidths=[5*cm, 12*cm])
    member_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
        ('FONTNAME', (1, 0), (1, -1), FONT_BOLD),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(member_table)
    story.append(Spacer(1, 15))
    
    # Payment Amount - Big highlight
    story.append(Paragraph("SỐ TIỀN CẦN ĐÓNG", section_title))
    
    amount_style = ParagraphStyle(
        'AmountStyle',
        fontName=FONT_BOLD,
        fontSize=28,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#059669'),
        spaceAfter=20
    )
    story.append(Paragraph(format_currency(amount), amount_style))
    story.append(Spacer(1, 10))
    
    # Bank Transfer Info & QR Code
    story.append(Paragraph("THÔNG TIN CHUYỂN KHOẢN", section_title))
    
    if bank_name and bank_account_number:
        # Create QR code
        qr_data = generate_vietqr_url(
            bank_code=bank_name,
            account_number=bank_account_number,
            amount=amount,
            content=payment_code or f"HUI {member_name}",
            account_name=bank_account_name
        )
        
        # Generate local QR code from transfer info
        transfer_info = f"Ngan hang: {bank_name}\nSo TK: {bank_account_number}\nSo tien: {int(amount)}\nNoi dung: {payment_code}"
        qr_image = generate_qr_code_image(transfer_info, size=150)
        
        # Bank info table with QR
        bank_info = [
            ["Ngân hàng:", bank_name],
            ["Số tài khoản:", bank_account_number],
            ["Chủ tài khoản:", bank_account_name or "N/A"],
            ["Nội dung CK:", payment_code or "N/A"],
        ]
        
        bank_table = Table(bank_info, colWidths=[5*cm, 12*cm])
        bank_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
            ('FONTNAME', (1, 0), (1, -1), FONT_BOLD),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (1, 2), (1, 2), colors.HexColor('#f0fdf4')),  # Highlight account name
            ('BACKGROUND', (1, 3), (1, 3), colors.HexColor('#fef3c7')),  # Highlight content
        ]))
        story.append(bank_table)
        story.append(Spacer(1, 20))
        
        # QR Code section
        story.append(Paragraph("QUÉT MÃ QR ĐỂ THANH TOÁN", section_title))
        
        # Add QR image
        qr_img = Image(qr_image, width=5*cm, height=5*cm)
        qr_table = Table([[qr_img]], colWidths=[17*cm])
        qr_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(qr_table)
        
        # VietQR note
        qr_note = ParagraphStyle(
            'QRNote',
            fontName=FONT_NAME,
            fontSize=9,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#64748b'),
            spaceAfter=10
        )
        story.append(Spacer(1, 10))
        story.append(Paragraph("Hoặc mở app ngân hàng và quét mã VietQR bên dưới:", qr_note))
        story.append(Paragraph(f"<link href='{qr_data}'>{qr_data[:50]}...</link>", qr_note))
    
    else:
        story.append(Paragraph("Thông tin ngân hàng chưa được cấu hình", normal_style))
    
    story.append(Spacer(1, 20))
    
    # Owner contact
    if owner_name or owner_phone:
        story.append(Paragraph("LIÊN HỆ CHỦ HỤI", section_title))
        owner_info = []
        if owner_name:
            owner_info.append(["Họ tên:", owner_name])
        if owner_phone:
            owner_info.append(["Điện thoại:", owner_phone])
        
        if owner_info:
            owner_table = Table(owner_info, colWidths=[5*cm, 12*cm])
            owner_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
                ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(owner_table)
    
    story.append(Spacer(1, 30))
    
    # Footer
    footer_style = ParagraphStyle(
        'Footer',
        fontName=FONT_NAME,
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#94a3b8')
    )
    story.append(Paragraph("─" * 60, footer_style))
    story.append(Paragraph("Vui lòng ghi đúng nội dung chuyển khoản để hệ thống tự động xác nhận.", footer_style))
    story.append(Paragraph("HuiManager Pro - Quản lý hụi chuyên nghiệp", footer_style))
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    
    return buffer


def generate_consolidated_bill_pdf(
    member_name: str,
    member_phone: str,
    bill_items: list,  # List of dicts with hui info
    total_amount: float,
    bill_date: datetime,
    owner_name: str = None,
    owner_phone: str = None
) -> io.BytesIO:
    """Generate a single consolidated PDF bill for multiple hui groups"""
    
    buffer = io.BytesIO()
    
    # Create document
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )
    
    # Custom styles with Vietnamese font
    title_style = ParagraphStyle(
        'Title',
        fontName=FONT_BOLD,
        fontSize=20,
        alignment=TA_CENTER,
        spaceAfter=10,
        textColor=colors.HexColor('#1e40af')
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        fontName=FONT_NAME,
        fontSize=12,
        alignment=TA_CENTER,
        spaceAfter=20,
        textColor=colors.HexColor('#64748b')
    )
    
    section_title = ParagraphStyle(
        'SectionTitle',
        fontName=FONT_BOLD,
        fontSize=14,
        spaceAfter=10,
        textColor=colors.HexColor('#0f172a'),
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        fontName=FONT_NAME,
        fontSize=11,
        leading=16
    )
    
    # Build content
    story = []
    
    # Header
    story.append(Paragraph("BILL THANH TOÁN HỤI", title_style))
    date_str = bill_date.strftime('%d/%m/%Y') if bill_date else datetime.now().strftime('%d/%m/%Y')
    story.append(Paragraph(f"Ngày {date_str} - HuiManager Pro", subtitle_style))
    story.append(Spacer(1, 10))
    
    # Member Info
    story.append(Paragraph("THÔNG TIN THÀNH VIÊN", section_title))
    
    member_data = [
        ["Họ tên:", member_name],
        ["Số điện thoại:", member_phone or "N/A"],
    ]
    
    member_table = Table(member_data, colWidths=[5*cm, 12*cm])
    member_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
        ('FONTNAME', (1, 0), (1, -1), FONT_BOLD),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(member_table)
    story.append(Spacer(1, 15))
    
    # Bill Items - Table
    story.append(Paragraph(f"CHI TIẾT {len(bill_items)} DÂY HỤI CẦN ĐÓNG", section_title))
    
    # Table header
    table_data = [["STT", "Tên dây hụi", "Kỳ", "Số chân", "Số tiền"]]
    
    for idx, item in enumerate(bill_items, 1):
        slot_text = f"{item['slot_count']}" if item['slot_count'] > 1 else "1"
        table_data.append([
            str(idx),
            item['hui_group_name'],
            f"{item['cycle_number']}/{item['total_cycles']}",
            slot_text,
            format_currency(item['amount'])
        ])
    
    # Total row
    table_data.append(["", "", "", "TỔNG CỘNG:", format_currency(total_amount)])
    
    items_table = Table(table_data, colWidths=[1.5*cm, 7*cm, 2.5*cm, 2*cm, 4*cm])
    items_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        # Header row
        ('FONTNAME', (0, 0), (-1, 0), FONT_BOLD),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        # Data rows
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # STT
        ('ALIGN', (2, 1), (2, -1), 'CENTER'),  # Kỳ
        ('ALIGN', (3, 1), (3, -1), 'CENTER'),  # Số chân
        ('ALIGN', (4, 1), (4, -1), 'RIGHT'),   # Số tiền
        # Total row
        ('FONTNAME', (0, -1), (-1, -1), FONT_BOLD),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0fdf4')),
        ('TEXTCOLOR', (4, -1), (4, -1), colors.HexColor('#059669')),
        # Grid
        ('GRID', (0, 0), (-1, -2), 0.5, colors.HexColor('#e2e8f0')),
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor('#059669')),
        # Padding
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 20))
    
    # Total Amount - Big highlight
    amount_style = ParagraphStyle(
        'AmountStyle',
        fontName=FONT_BOLD,
        fontSize=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#059669'),
        spaceAfter=10
    )
    story.append(Paragraph(f"TỔNG TIỀN CẦN ĐÓNG: {format_currency(total_amount)}", amount_style))
    story.append(Spacer(1, 20))
    
    # Bank Transfer Info - Use first item's bank info
    if bill_items and bill_items[0].get('bank_name') and bill_items[0].get('bank_account_number'):
        first_item = bill_items[0]
        story.append(Paragraph("THÔNG TIN CHUYỂN KHOẢN", section_title))
        
        # Combine all payment codes
        payment_codes = [item['payment_code'] for item in bill_items if item.get('payment_code')]
        combined_code = payment_codes[0] if len(payment_codes) == 1 else f"TONG {member_name[:10].upper()}"
        
        bank_info = [
            ["Ngân hàng:", first_item['bank_name']],
            ["Số tài khoản:", first_item['bank_account_number']],
            ["Chủ tài khoản:", first_item.get('bank_account_name') or "N/A"],
            ["Nội dung CK:", combined_code],
        ]
        
        bank_table = Table(bank_info, colWidths=[5*cm, 12*cm])
        bank_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
            ('FONTNAME', (1, 0), (1, -1), FONT_BOLD),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (1, 3), (1, 3), colors.HexColor('#fef3c7')),  # Highlight content
        ]))
        story.append(bank_table)
        story.append(Spacer(1, 15))
        
        # QR Code
        story.append(Paragraph("QUÉT MÃ QR ĐỂ THANH TOÁN", section_title))
        
        transfer_info = f"Ngan hang: {first_item['bank_name']}\nSo TK: {first_item['bank_account_number']}\nSo tien: {int(total_amount)}\nNoi dung: {combined_code}"
        qr_image = generate_qr_code_image(transfer_info, size=150)
        
        qr_img = Image(qr_image, width=5*cm, height=5*cm)
        qr_table = Table([[qr_img]], colWidths=[17*cm])
        qr_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(qr_table)
    
    story.append(Spacer(1, 20))
    
    # Owner contact
    if owner_name or owner_phone:
        story.append(Paragraph("LIÊN HỆ CHỦ HỤI", section_title))
        owner_info = []
        if owner_name:
            owner_info.append(["Họ tên:", owner_name])
        if owner_phone:
            owner_info.append(["Điện thoại:", owner_phone])
        
        if owner_info:
            owner_table = Table(owner_info, colWidths=[5*cm, 12*cm])
            owner_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748b')),
                ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0f172a')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(owner_table)
    
    story.append(Spacer(1, 20))
    
    # Footer
    footer_style = ParagraphStyle(
        'Footer',
        fontName=FONT_NAME,
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#94a3b8')
    )
    story.append(Paragraph("─" * 60, footer_style))
    story.append(Paragraph("Vui lòng chuyển khoản đúng số tiền và nội dung để được xác nhận tự động.", footer_style))
    story.append(Paragraph("HuiManager Pro - Quản lý hụi chuyên nghiệp", footer_style))
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    
    return buffer
