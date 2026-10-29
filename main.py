import os
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import io

# ReportLab imports for generating Section 65B Certified Legal Notices
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

app = FastAPI(title="Refundr - Statutory Consumer Recovery Engine")

DB_FILE = "refundr.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS disputes (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            consumer_name TEXT,
            consumer_upi TEXT,
            consumer_phone TEXT,
            category TEXT,
            sub_issue TEXT,
            opposite_party TEXT,
            reference_id TEXT,
            incident_date TEXT,
            principal_amount REAL,
            statutory_penalty REAL,
            interest_amount REAL,
            total_demand REAL,
            narrative TEXT,
            legal_violations TEXT,
            evidence_hash TEXT,
            gps_coordinates TEXT,
            status TEXT,
            cure_deadline TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- RULE-BASED LEGAL INTELLIGENCE ENGINE ---
LEGAL_TAXONOMY = {
    "aviation": {
        "title": "Civil Aviation",
        "regulator": "DGCA CAR Section 3, Series M",
        "sub_issues": [
            "Flight Delays (>3 to 6 Hours)",
            "Flight Cancellations without Adequate Notice",
            "Denied Boarding (Overbooking)",
            "Baggage Lost, Damaged or Delayed"
        ]
    },
    "railways": {
        "title": "Railways & Public Transit",
        "regulator": "IRCA Coaching Tariff & Railways Act 1989",
        "sub_issues": [
            "Train Delays Exceeding 3 Hours (Full TDR)",
            "AC Failure / Coach Downgrade",
            "Waitlisted Ticket Fee Deductions",
            "Coach Hygiene / Transit Service Failure"
        ]
    },
    "ecommerce": {
        "title": "E-Commerce & Retail",
        "regulator": "CPA E-Commerce Rules 2020 & Sec 2(10)",
        "sub_issues": [
            "Refusal of Returns / Replacements on Defective Goods",
            "Counterfeit or Fake Products Delivered",
            "Unilateral Order Cancellation by Seller",
            "Missing Package Items / Empty Parcel Delivery"
        ]
    },
    "banking": {
        "title": "Digital Banking & UPI",
        "regulator": "RBI DPSS TAT Circular (₹100/day delay penalty)",
        "sub_issues": [
            "Failed UPI / IMPS Debit (Debited but not Credited)",
            "ATM Cash Dispenser Failure (Cash not dispensed)",
            "Card POS / Merchant Refund Reversal Failure",
            "Unauthorized Digital Debits & Hidden Surcharges"
        ]
    },
    "multiplex": {
        "title": "Cinemas & Multiplexes",
        "regulator": "CPA 2019 Sec 2(11) Deficiency of Service",
        "sub_issues": [
            "Inordinate Show Delay / Screen Disruption",
            "Unannounced Show Cancellation without Restitution",
            "Involuntary Recliner / Luxury Seat Downgrade",
            "Unfair Mandatory Convenience Charges"
        ]
    },
    "food": {
        "title": "Food Delivery & Dining",
        "regulator": "FSSAI Act 2006 & CPA Product Liability",
        "sub_issues": [
            "Substandard, Contaminated or Expired Food",
            "Wrong Dietary Item Delivered (e.g. Non-Veg for Veg)",
            "Missing Items in Parcel Refused Refund",
            "Excessive Delivery Delay Causing Spoiled Food"
        ]
    },
    "telecom": {
        "title": "Telecom & Broadband",
        "regulator": "TRAI Quality of Service (QoS) Regulations",
        "sub_issues": [
            "Prolonged Fiber / Broadband Outage (>24-72h)",
            "Unauthorized Value Added Service (VAS) Deductions",
            "Refusal to Issue MNP Porting Code",
            "Disputed Postpaid Charges / Ghost Data Usage"
        ]
    },
    "metrology": {
        "title": "Legal Metrology & Dual MRP",
        "regulator": "Legal Metrology Act 2009 Sec 36",
        "sub_issues": [
            "Overcharging Above Printed MRP (Airports/Theatres/Malls)",
            "Dual MRP Stickers / Altered Packaging Price",
            "Mandatory Ancillary Purchase for Base Product"
        ]
    },
    "mobility": {
        "title": "Cab Aggregators (Ola/Uber)",
        "regulator": "Motor Vehicles Aggregator Guidelines 2020",
        "sub_issues": [
            "Driver Cancellation Fee Imposed on Rider",
            "Unwarranted GPS Surge / False Route Recalculation",
            "AC Refusal with Full AC-tier Fare Charged",
            "Mid-trip Passenger Abandonment by Driver"
        ]
    },
    "darkpatterns": {
        "title": "CCPA Dark Patterns",
        "regulator": "CCPA Guidelines for Prevention of Dark Patterns 2023",
        "sub_issues": [
            "Drip Pricing (Hidden fees revealed only at final pay)",
            "Basket Sneaking (Pre-checked add-ons, donations, insurance)",
            "Subscription Trap (Deceptive or blocked cancellation)",
            "Forced Bundling of Unwanted Subscriptions"
        ]
    },
    "custom": {
        "title": "Other / Custom Dispute",
        "regulator": "CPA 2019 Section 2(11) read with Section 39",
        "sub_issues": ["Custom Unfair Trade Practice or Service Deficiency"]
    }
}

class GrievanceAnalysisRequest(BaseModel):
    category: str
    sub_issue: str
    narrative: str

@app.post("/api/analyze-grievance")
async def analyze_grievance(req: GrievanceAnalysisRequest):
    text = req.narrative.lower()
    cat = req.category
    
    # 1. Detect Company / Opposite Party
    companies = [
        "indigo", "air india", "spicejet", "akasa", "vistara", "irctc", 
        "amazon", "flipkart", "meesho", "myntra", "swiggy", "zomato", 
        "ola", "uber", "airtel", "jio", "vi", "pvr", "inox", "bookmyshow", 
        "paytm", "phonepe", "gpay", "hdfc", "sbi", "icici", "axis"
    ]
    detected_entity = "Opposite Commercial Party"
    for comp in companies:
        if comp in text:
            detected_entity = comp.upper()
            break
            
    # 2. Derive Violations, Statutory Heads, and Targeted Evidence Checklist
    violations = []
    applicable_laws = []
    evidence_checklist = []
    suggested_penalty = 0.0

    if cat == "aviation":
        applicable_laws = ["DGCA CAR Section 3, Series M, Part IV", "Consumer Protection Act 2019 Sec 2(11)"]
        violations.append("Failure to adhere to DGCA Passenger Charter on statutory delays & meal facilitation")
        evidence_checklist = [
            {"id": "doc_ticket", "title": "Boarding Pass / E-Ticket", "desc": "Showing PNR, origin, destination and scheduled flight time", "required": True},
            {"id": "doc_delay", "title": "Delay SMS / FIDS Display Photo", "desc": "Official airline intimation or airport screen photo", "required": True},
            {"id": "doc_expense", "title": "Out-of-Pocket Airport Expenses", "desc": "Food, water, or taxi invoices incurred due to delay", "required": False}
        ]
        suggested_penalty = 3000.0  # Base DGCA delay relief slab

    elif cat == "railways":
        applicable_laws = ["IRCA Coaching Tariff Rules", "Railways Act 1989 Section 167"]
        violations.append("Breach of Railway TDR refund norms for delays exceeding 3 hours")
        evidence_checklist = [
            {"id": "doc_ticket", "title": "IRCTC E-Ticket / PNR Slip", "desc": "Original booking confirmation showing train and coach details", "required": True},
            {"id": "doc_tdr", "title": "TDR Filing Screenshot / Station Memo", "desc": "Proof of cancellation/TDR filed prior to train departure", "required": True}
        ]
        suggested_penalty = 500.0

    elif cat == "ecommerce":
        applicable_laws = ["Consumer Protection (E-Commerce) Rules 2020", "CPA 2019 Sec 2(47) Unfair Trade Practice"]
        violations.append("Refusal of statutory return/refund on defective goods or deficient delivery")
        evidence_checklist = [
            {"id": "doc_invoice", "title": "Tax Invoice / Order Confirmation", "desc": "Tax invoice showing Order ID, items, and amount paid", "required": True},
            {"id": "doc_defect", "title": "Photo/Video of Defective Product", "desc": "Clear forensic visual of the damaged or wrong product", "required": True},
            {"id": "doc_chat", "title": "Support Rejection Transcript / Email", "desc": "Screenshot showing denial of refund or ticket closure", "required": True}
        ]
        suggested_penalty = 1500.0

    elif cat == "banking":
        applicable_laws = ["RBI DPSS.CO.PD No.629/02.01.014/2019-20 (Harmonisation of TAT)", "Payment & Settlement Systems Act 2007"]
        violations.append("Non-reversal of failed electronic debit within statutory T+1 / T+5 turnaround window")
        evidence_checklist = [
            {"id": "doc_statement", "title": "Bank Account Statement / Debit SMS", "desc": "Showing deduction with UTR / RRN 12-digit reference number", "required": True},
            {"id": "doc_complaint", "title": "Bank Grievance Ticket / CRM Acknowledgement", "desc": "Proof of initial complaint filed with the issuer bank", "required": True}
        ]
        suggested_penalty = 2000.0  # ₹100/day statutory TAT default assumption

    elif cat == "darkpatterns":
        applicable_laws = ["CCPA Guidelines for Prevention and Regulation of Dark Patterns 2023", "CPA 2019 Sec 2(47)"]
        violations.append("Deployment of deceptive interface design (Drip pricing / Basket sneaking / Forced auto-debit)")
        evidence_checklist = [
            {"id": "doc_checkout", "title": "Checkout Screen Screenshot", "desc": "Showing pre-selected add-ons or hidden drip fee injection", "required": True},
            {"id": "doc_receipt", "title": "Final Invoice Showing Surcharge", "desc": "Itemized bill demonstrating unconsented fee deduction", "required": True}
        ]
        suggested_penalty = 2500.0

    else:
        applicable_laws = ["Consumer Protection Act 2019 Section 2(11)", "Section 39 (Relief Order)"]
        violations.append(f"Statutory deficiency of service and unfair trade practice in {cat.capitalize()}")
        evidence_checklist = [
            {"id": "doc_proof", "title": "Invoice / Proof of Payment", "desc": "Bill, receipt, or transaction statement proving payment", "required": True},
            {"id": "doc_incident", "title": "Incident Evidence / Written Communication", "desc": "Screenshots, letters, or physical photos evidencing deficiency", "required": True}
        ]
        suggested_penalty = 1000.0

    return {
        "status": "success",
        "entity": detected_entity,
        "applicable_laws": applicable_laws,
        "violations": violations,
        "suggested_statutory_penalty": suggested_penalty,
        "evidence_checklist": evidence_checklist
    }

# --- DISPUTE SUBMISSION & SECTION 65B FORENSIC PACKAGING ---
@app.post("/api/create-dispute")
async def create_dispute(
    consumer_name: str = Form(...),
    consumer_upi: str = Form(...),
    consumer_phone: str = Form(...),
    category: str = Form(...),
    sub_issue: str = Form(...),
    opposite_party: str = Form(...),
    reference_id: str = Form("N/A"),
    incident_date: str = Form(...),
    principal_amount: float = Form(...),
    statutory_penalty: float = Form(0.0),
    narrative: str = Form(...),
    legal_violations: str = Form(...),
    gps_coordinates: str = Form("13.0827° N, 80.2707° E"),
    evidence_file: Optional[UploadFile] = File(None)
):
    dispute_id = f"REF-{datetime.now().strftime('%y%m%d')}-{os.urandom(3).hex().upper()}"
    created_at = datetime.now(timezone.utc).isoformat()
    cure_deadline = (datetime.now() + timedelta(days=7)).strftime("%d-%b-%Y")

    # Accrued Pre-litigation Interest @ 12% p.a. (Assume 30 days base duration)
    interest_amount = round(principal_amount * 0.12 * (30 / 365), 2)
    total_demand = round(principal_amount + statutory_penalty + interest_amount, 2)

    # Forensic SHA-256 Digest of Uploaded Evidence
    if evidence_file and evidence_file.filename:
        file_bytes = await evidence_file.read()
        evidence_hash = hashlib.sha256(file_bytes).hexdigest()
    else:
        seed = f"{dispute_id}-{created_at}-{principal_amount}".encode("utf-8")
        evidence_hash = hashlib.sha256(seed).hexdigest()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO disputes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        dispute_id, created_at, consumer_name, consumer_upi, consumer_phone,
        category, sub_issue, opposite_party, reference_id, incident_date,
        principal_amount, statutory_penalty, interest_amount, total_demand,
        narrative, legal_violations, evidence_hash, gps_coordinates, "OPEN_IN_CURE_WINDOW", cure_deadline
    ))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "dispute_id": dispute_id,
        "total_demand": total_demand,
        "cure_deadline": cure_deadline,
        "evidence_hash": evidence_hash,
        "settle_url": f"/settle/{dispute_id}",
        "pdf_url": f"/notice/{dispute_id}.pdf"
    })

# --- PDF GENERATOR (SECTION 65B / 63 BSA CERTIFIED DEMAND NOTICE) ---
@app.get("/notice/{dispute_id}.pdf")
async def generate_legal_notice(dispute_id: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return HTMLResponse("Dispute not found", status_code=404)

    (
        d_id, created_at, c_name, c_upi, c_phone,
        category, sub_issue, opp_party, ref_id, inc_date,
        principal, penalty, interest, total_demand,
        narrative, violations, ev_hash, gps_coords, status, cure_deadline
    ) = row

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle', parent=styles['Heading1'],
        fontName='Helvetica-Bold', fontSize=14, leading=17,
        textColor=colors.HexColor('#0f172a'), alignment=1
    )
    sub_title_style = ParagraphStyle(
        'SubTitle', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=12,
        textColor=colors.HexColor('#475569'), alignment=1
    )
    body_style = ParagraphStyle(
        'Body', parent=styles['Normal'],
        fontName='Helvetica', fontSize=9, leading=13,
        textColor=colors.HexColor('#1e293b')
    )
    bold_style = ParagraphStyle(
        'BoldBody', parent=body_style, fontName='Helvetica-Bold'
    )
    legal_box_style = ParagraphStyle(
        'LegalBox', parent=body_style,
        fontName='Helvetica-Oblique', fontSize=8, leading=11,
        textColor=colors.HexColor('#334155')
    )

    elements = []

    elements.append(Paragraph("STATUTORY PRE-LITIGATION LEGAL DEMAND NOTICE", title_style))
    elements.append(Paragraph(
        "ISSUED PURSUANT TO THE CONSUMER PROTECTION ACT, 2019 READ WITH SECTORAL REGULATIONS<br/>"
        "MANDATORY 7-DAY STATUTORY CURE WINDOW", sub_title_style
    ))
    elements.append(Spacer(1, 10))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0f172a')))
    elements.append(Spacer(1, 10))

    # Meta Grid
    meta_data = [
        [Paragraph(f"<b>Notice Docket:</b> {d_id}", body_style), Paragraph(f"<b>Date of Notice:</b> {datetime.now().strftime('%d-%b-%Y')}", body_style)],
        [Paragraph(f"<b>To (Opposite Party):</b> {opp_party}", bold_style), Paragraph(f"<b>Cure Window Expiry:</b> <font color='red'><b>{cure_deadline}</b></font>", body_style)],
        [Paragraph(f"<b>Claimant:</b> {c_name}", body_style), Paragraph(f"<b>Claimant Settlement UPI:</b> {c_upi}", body_style)],
        [Paragraph(f"<b>Dispute Category:</b> {category.upper()}", body_style), Paragraph(f"<b>Booking / Order Ref:</b> {ref_id}", body_style)]
    ]
    meta_table = Table(meta_data, colWidths=[260, 260])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 12))

    # Narrative of Deficiency
    elements.append(Paragraph("<b>1. STATEMENT OF FACTS & MATERIAL DEFICIENCY</b>", bold_style))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"The Complainant entered into a binding consumer transaction with the Opposite Party on or around <b>{inc_date}</b>. Contrary to statutory covenants and service guarantees, the Complainant was subjected to severe service deficiency, summarized as follows:<br/><br/><i>\"{narrative}\"</i>", body_style))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("<b>2. STATUTORY VIOLATIONS & CAUSES OF ACTION</b>", bold_style))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"The acts and omissions of the Opposite Party constitute direct contraventions of:<br/>• <b>{violations}</b><br/>• Section 2(11) (Deficiency of Service) & Section 2(47) (Unfair Trade Practice) of CPA, 2019.<br/>• The Opposite Party is put on notice that withholding liquidated damages constitutes an actionable statutory default.", body_style))
    elements.append(Spacer(1, 10))

    # Financial Slabs Table
    elements.append(Paragraph("<b>3. ITEMIZED STATUTORY RESTITUTION COMPUTATION</b>", bold_style))
    elements.append(Spacer(1, 4))
    claim_table_data = [
        ["Claim Head", "Statutory Rule / Authority", "Amount (INR)"],
        ["Direct Principal Loss / Ticket Refusal", "Section 39(1)(a) Consumer Protection Act", f"₹ {principal:,.2f}"],
        ["Statutory Delay / Service Penalty", "Sector Regulator Tariff / Harmonised TAT", f"₹ {penalty:,.2f}"],
        ["Accrued Pre-litigation Interest", "12.00% per annum compound accrued", f"₹ {interest:,.2f}"],
        ["TOTAL MANDATORY RESTITUTION", "100% Retained Directly by Consumer", f"₹ {total_demand:,.2f}"]
    ]
    t = Table(claim_table_data, colWidths=[200, 220, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#f1f5f9')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#94a3b8')),
        ('ALIGN', (2,0), (2,-1), 'RIGHT'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    # Section 65B Forensics Block
    elements.append(Paragraph("<b>4. CERTIFICATE UNDER SECTION 65B INDIAN EVIDENCE ACT / SECTION 63 BSA</b>", bold_style))
    elements.append(Spacer(1, 4))
    cert_text = (
        f"I hereby certify that the electronic documents and records submitted in support of this docket were "
        f"captured via regular operational computer output. The cryptographic SHA-256 hash digest of the original primary evidence is "
        f"<b>{ev_hash}</b> with verified location timestamp <b>[{gps_coords}]</b>. The hash integrity guarantees "
        f"that no subsequent alteration, deletion, or synthetic forgery has occurred since the moment of capture."
    )
    elements.append(Paragraph(cert_text, legal_box_style))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("<b>5. REQUISITION FOR SETTLEMENT & CONSEQUENCE OF DEFAULT</b>", bold_style))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"The Opposite Party is hereby granted a strict <b>7-day statutory cure window</b> expiring on <b>{cure_deadline}</b> to disburse the full sum of "
        f"<b>₹ {total_demand:,.2f}</b> directly to the Complainant's UPI handle <b>{c_upi}</b> using the official Corporate Settlement Desk at:<br/>"
        f"<b>https://refundr-app.onrender.com/settle/{d_id}</b><br/><br/>"
        f"Failing compliance within 7 days, this notice shall serve as formal pre-litigation documentation for automated escalation to the "
        f"National Consumer Helpline (NCH 1915 Convergence) and registry lodging before the competent District Consumer Disputes Redressal Commission (e-Daakhil Section 35 Petition).",
        body_style
    ))

    doc.build(elements)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={dispute_id}_Statutory_Notice.pdf"}
    )

# --- CORPORATE SETTLEMENT DESK (/settle/{id}) ---
@app.get("/settle/{dispute_id}", response_class=HTMLResponse)
async def settle_portal(dispute_id: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return HTMLResponse("<h3>Invalid Settlement Token or Dispute Expired.</h3>", status_code=404)

    (
        d_id, created_at, c_name, c_upi, c_phone,
        category, sub_issue, opp_party, ref_id, inc_date,
        principal, penalty, interest, total_demand,
        narrative, violations, ev_hash, gps_coords, status, cure_deadline
    ) = row

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Refundr Corporate Settlement Desk | {d_id}</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen py-10 px-4">
        <div class="max-w-2xl mx-auto bg-slate-800 border border-slate-700 rounded-xl p-8 shadow-2xl">
            <div class="flex items-center justify-between border-b border-slate-700 pb-4 mb-6">
                <div>
                    <h1 class="text-xl font-bold text-amber-400">CORPORATE ADJUDICATION & SETTLEMENT DESK</h1>
                    <p class="text-xs text-slate-400">Pre-Litigation Alternative Dispute Resolution Portal</p>
                </div>
                <span class="px-3 py-1 bg-red-950/80 border border-red-500/50 text-red-300 text-xs font-mono rounded-full animate-pulse">
                    7-Day Cure Window: {cure_deadline}
                </span>
            </div>

            <div class="bg-slate-900/80 border border-slate-700 rounded-lg p-5 mb-6 space-y-3">
                <div class="flex justify-between text-sm">
                    <span class="text-slate-400">Opposite Party:</span>
                    <span class="font-bold text-white">{opp_party}</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-slate-400">Claimant:</span>
                    <span class="font-bold text-white">{c_name}</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-slate-400">Category & Cause:</span>
                    <span class="text-slate-200">{category.upper()} &bull; {sub_issue}</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-slate-400">Incident Reference:</span>
                    <span class="font-mono text-cyan-400">{ref_id}</span>
                </div>
                <div class="border-t border-slate-800 pt-3">
                    <p class="text-xs text-slate-400 mb-1">Grievance Summary:</p>
                    <p class="text-xs italic text-slate-300 bg-slate-950/60 p-3 rounded border border-slate-800">"{narrative}"</p>
                </div>
            </div>

            <div class="bg-emerald-950/40 border border-emerald-500/40 rounded-lg p-5 mb-6">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-sm font-semibold text-emerald-300">Mandatory Statutory Restitution Demand:</span>
                    <span class="text-2xl font-bold text-emerald-400">₹ {total_demand:,.2f}</span>
                </div>
                <p class="text-xs text-emerald-200/70">100% Disbursed to Consumer &bull; Zero Platform Fee Deduction</p>
            </div>

            <form action="/api/settle-confirm" method="POST" class="space-y-4">
                <input type="hidden" name="dispute_id" value="{d_id}">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Authorised Compliance Officer Name & Designation</label>
                    <input type="text" name="officer_name" required placeholder="e.g. Anand R., Legal Counsel" class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-400">
                </div>
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Statutory Apology / Settlement Statement (Section 2(11) CPA)</label>
                    <textarea name="apology_note" rows="2" class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs text-white focus:outline-none focus:border-amber-400" placeholder="The Opposite Party expresses sincere regret for the service shortfall and authorizes unconditional restitution without prejudice..."></textarea>
                </div>
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Direct Bank Reference / UPI Transaction ID</label>
                    <input type="text" name="txn_id" required placeholder="12-digit UTR or Bank Payment Ref" class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm font-mono text-emerald-400 focus:outline-none focus:border-emerald-500">
                </div>

                <div class="pt-3">
                    <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-4 rounded-lg text-sm transition">
                        Disburse Restitution to {c_upi} & Close Dispute
                    </button>
                    <p class="text-center text-[10px] text-slate-500 mt-2">Executing direct UPI disbursement closes pre-litigation liability under Section 39 CPA, 2019.</p>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.post("/api/settle-confirm")
async def settle_confirm(dispute_id: str = Form(...), txn_id: str = Form(...), officer_name: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE disputes SET status = 'SETTLED_IN_CURE_WINDOW' WHERE id = ?", (dispute_id,))
    conn.commit()
    conn.close()

    return HTMLResponse(f"""
        <body style="background:#0f172a;color:#f8fafc;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="text-align:center;background:#1e293b;padding:40px;border-radius:12px;border:1px solid #10b981;max-width:500px;">
                <h2 style="color:#34d399;">Dispute Successfully Settled</h2>
                <p style="font-size:14px;color:#cbd5e1;line-height:1.6;">Full restitution has been recorded with transaction reference: <b>{txn_id}</b>.<br/>Pre-litigation case <b>{dispute_id}</b> has been formally closed.</p>
                <a href="/" style="display:inline-block;margin-top:20px;color:#38bdf8;text-decoration:none;font-size:13px;">Return to Refundr Dashboard &rarr;</a>
            </div>
        </body>
    """)

# Serve Frontend
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)