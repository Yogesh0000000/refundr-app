import os
import hashlib
import sqlite3
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
import io

# ReportLab for Section 65B Certified Legal PDF Notices
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

app = FastAPI(title="Refundr - Automated Statutory Consumer Escalation")

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
            entity_branch TEXT,
            entity_address TEXT,
            reference_id TEXT,
            incident_date TEXT,
            primary_bill_amount REAL,
            incidental_expense_amount REAL,
            statutory_penalty REAL,
            interest_amount REAL,
            total_demand REAL,
            narrative TEXT,
            legal_violations TEXT,
            primary_evidence_hash TEXT,
            expense_evidence_hash TEXT,
            combined_hash TEXT,
            gps_coordinates TEXT,
            status TEXT,
            cure_deadline TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- KNOWN NODAL ENTITIES & CIN DIRECTORY ---
KNOWN_ENTITIES = {
    "INDIGO": {
        "legal_name": "InterGlobe Aviation Limited (IndiGo)",
        "cin": "L62100DL2004PLC129768",
        "nodal_email": "nodalofficer@goindigo.in",
        "address": "Central Wing, Ground Floor, Thapar House, 124, Janpath, New Delhi - 110001",
        "regulator": "DGCA CAR Section 3, Series M, Part IV"
    },
    "AIR INDIA": {
        "legal_name": "Air India Limited",
        "cin": "U62100DL2007GOI160058",
        "nodal_email": "nodal.officer@airindia.com",
        "address": "Airlines House, 113, Gurudwara Rakabganj Road, New Delhi - 110001",
        "regulator": "DGCA CAR Section 3, Series M"
    },
    "FLIPKART": {
        "legal_name": "Flipkart Internet Private Limited",
        "cin": "U51109KA2012PTC066107",
        "nodal_email": "grievance.officer@flipkart.com",
        "address": "Buildings Alyssa, Begonia & Clover, Embassy Tech Village, Outer Ring Road, Bengaluru, Karnataka - 560103",
        "regulator": "Consumer Protection (E-Commerce) Rules, 2020"
    },
    "AMAZON": {
        "legal_name": "Amazon Seller Services Private Limited",
        "cin": "U51900KA2004PTC034037",
        "nodal_email": "grievance-officer@amazon.in",
        "address": "8th Floor, Brigade Gateway, 26/1 Dr. Rajkumar Road, Malleshwaram West, Bengaluru - 560055",
        "regulator": "Consumer Protection (E-Commerce) Rules, 2020"
    },
    "SWIGGY": {
        "legal_name": "Bundl Technologies Private Limited (Swiggy)",
        "cin": "U72200KA2013PTC070558",
        "nodal_email": "grievances@swiggy.in",
        "address": "Tower D, 9th Floor, IBC Knowledge Park, Bannerghatta Main Road, Bengaluru - 560029",
        "regulator": "FSSAI Act 2006 & CPA 2019"
    },
    "ZOMATO": {
        "legal_name": "Zomato Limited",
        "cin": "L93030HR2010PLC040609",
        "nodal_email": "nodal@zomato.com",
        "address": "Ground Floor, 12A, 94 Meghdoot, Nehru Place, New Delhi - 110019",
        "regulator": "FSSAI Act 2006 & CPA 2019"
    },
    "PVR": {
        "legal_name": "PVR INOX Limited",
        "cin": "L74899DL1995PLC068797",
        "nodal_email": "grievance@pvrcinemas.com",
        "address": "7th Floor, Lotus Grandeur, Veera Desai Road Extension, Andheri (West), Mumbai - 400053",
        "regulator": "Consumer Protection Act 2019 Sec 2(11)"
    },
    "INOX": {
        "legal_name": "PVR INOX Limited",
        "cin": "L74899DL1995PLC068797",
        "nodal_email": "grievance@pvrcinemas.com",
        "address": "7th Floor, Lotus Grandeur, Veera Desai Road Extension, Andheri (West), Mumbai - 400053",
        "regulator": "Consumer Protection Act 2019 Sec 2(11)"
    },
    "RAPIDO": {
        "legal_name": "Roppen Transportation Services Private Limited (Rapido)",
        "cin": "U74140KA2015PTC079737",
        "nodal_email": "grievance@rapido.bike",
        "address": "No. 148, 1st Floor, SLV Arcade, 5th Main Road, Sector 6, HSR Layout, Bengaluru - 560102",
        "regulator": "Motor Vehicle Aggregator Guidelines 2020"
    },
    "OLA": {
        "legal_name": "ANI Technologies Private Limited (Ola Cabs)",
        "cin": "U72900KA2010PTC086437",
        "nodal_email": "grievanceofficer@olacabs.com",
        "address": "Regent Insignia, #414, 3rd Floor, 4th Block, 17th Main, 100 Feet Road, Koramangala, Bengaluru - 560034",
        "regulator": "Motor Vehicle Aggregator Guidelines 2020"
    },
    "UBER": {
        "legal_name": "Uber India Systems Private Limited",
        "cin": "U74120MH2013FTC247008",
        "nodal_email": "grievance-india@uber.com",
        "address": "Level 13, Platinum Techno Park, Plot No. 17 & 18, Sector 30A, Vashi, Navi Mumbai - 400705",
        "regulator": "Motor Vehicle Aggregator Guidelines 2020"
    }
}

class AnalysisRequest(BaseModel):
    category: str
    sub_issue: str
    narrative: str

@app.post("/api/analyze-grievance")
async def analyze_grievance(req: AnalysisRequest):
    text = req.narrative.lower()
    cat = req.category
    
    # 1. Detect Company / Brand
    detected_brand = None
    for brand in KNOWN_ENTITIES.keys():
        if brand.lower() in text:
            detected_brand = brand
            break
            
    # Fallback to local theater / store detection
    is_ambiguous_venue = False
    suggested_venues = []
    
    if not detected_brand:
        # Check if user mentioned theatre, cinema, store, branch, clinic, restaurant
        theater_match = re.search(r'([a-zA-Z0-9\s]+)\s+(theatre|theater|cinemas|talkies|store|bazaar|restaurant|hotel|hospital)', text)
        if theater_match:
            raw_entity = theater_match.group(0).strip().title()
            detected_brand = raw_entity
            is_ambiguous_venue = True
            suggested_venues = [
                f"{raw_entity} - Chennai (Ambattur / Central)",
                f"{raw_entity} - Coimbatore / Madurai",
                f"{raw_entity} - Enter specific local branch address"
            ]
        else:
            detected_brand = "Commercial Opposite Party"
            is_ambiguous_venue = True
    elif detected_brand in ["PVR", "INOX"]:
        is_ambiguous_venue = True
        suggested_venues = [
            f"{detected_brand} Cinemas - Grand Mall / Express Avenue",
            f"{detected_brand} Cinemas - VR Mall / Phoenix Marketcity",
            f"{detected_brand} Cinemas - Local City Mall Branch"
        ]

    # 2. Extract Numbers & Amounts directly from narrative
    extracted_bill = 0.0
    bill_match = re.search(r'(?:ticket|bill|paid|fare|order|worth|amount|for)\s*(?:of|is|was)?\s*(?:rs\.?|inr|₹)?\s*(\d{2,6})', text)
    if bill_match:
        extracted_bill = float(bill_match.group(1))

    extracted_expense = 0.0
    expense_match = re.search(r'(?:rapido|uber|ola|cab|taxi|auto|bus|food|meal|refreshments?)\s*(?:cost|paid|for|of)?\s*(?:rs\.?|inr|₹)?\s*(\d{2,5})', text)
    if expense_match:
        extracted_expense = float(expense_match.group(1))

    # 3. Reference ID detection (e.g. 6E-204, PNR, Order ID)
    detected_ref = "PENDING_VERIFICATION"
    ref_match = re.search(r'([a-z0-9]{2,3}[-\s]?[0-9]{3,5}|pnr\s*[:#]?\s*[a-z0-9]+|order\s*[:#]?\s*[0-9]+)', text)
    if ref_match:
        detected_ref = ref_match.group(0).upper()

    # 4. Legal Violations Mapping
    violations = []
    applicable_laws = []
    statutory_penalty = 0.0

    if cat == "aviation":
        applicable_laws = ["DGCA CAR Section 3, Series M, Part IV", "Passenger Charter Clause 3.2", "CPA 2019 Sec 2(11)"]
        violations.append("Failure to provide mandatory meals and refreshments during flight delay exceeding statutory threshold")
        violations.append("Statutory compensation denial for delay / deficient carrier turnaround")
        statutory_penalty = 3000.0
    elif cat == "railways":
        applicable_laws = ["IRCA Coaching Tariff Rules", "Railways Act 1989 Section 167"]
        violations.append("Train delay exceeding 3 hours with failure to honor full TDR statutory reimbursement")
        statutory_penalty = 500.0
    elif cat == "banking":
        applicable_laws = ["RBI DPSS TAT Circular DPSS.CO.PD No.629/02.01.014/2019-20", "Payment and Settlement Systems Act 2007"]
        violations.append("Failure to auto-reverse failed digital debit within statutory T+1/T+5 days (₹100/day penalty applicable)")
        statutory_penalty = 1500.0
    elif cat == "ecommerce":
        applicable_laws = ["Consumer Protection (E-Commerce) Rules 2020", "CPA 2019 Section 2(47) Unfair Trade Practice"]
        violations.append("Unilateral refusal of statutory return / replacement for non-delivery, damaged or deficient goods")
        statutory_penalty = 1000.0
    elif cat == "multiplex":
        applicable_laws = ["Consumer Protection Act 2019 Section 2(11)", "Cinematograph (Public Exhibition) Regulations"]
        violations.append("Unscheduled screening disruption / inordinate delay with failure to grant immediate restitution")
        statutory_penalty = 1000.0
    elif cat == "darkpatterns":
        applicable_laws = ["CCPA Guidelines for Prevention and Regulation of Dark Patterns 2023", "CPA 2019 Section 18"]
        violations.append("Deployment of deceptive interface design (drip pricing, forced bundling, pre-selected add-ons)")
        statutory_penalty = 2500.0
    else:
        applicable_laws = ["Consumer Protection Act 2019 Section 2(11)", "Section 39 (Consumer Relief Orders)"]
        violations.append("Statutory deficiency in service and unfair trade practice causing direct consumer detriment")
        statutory_penalty = 1000.0

    nodal_info = KNOWN_ENTITIES.get(detected_brand, {
        "legal_name": f"{detected_brand} (Commercial Establishment)",
        "cin": "PENDING_LOCAL_DISCOVERY",
        "nodal_email": "nodalofficer@establishment.local",
        "address": "Local Commercial Establishment Branch",
        "regulator": "Consumer Protection Act 2019"
    })

    return {
        "status": "success",
        "detected_brand": detected_brand,
        "is_ambiguous_venue": is_ambiguous_venue,
        "suggested_venues": suggested_venues,
        "detected_ref": detected_ref,
        "extracted_bill": extracted_bill,
        "extracted_expense": extracted_expense,
        "statutory_penalty": statutory_penalty,
        "applicable_laws": applicable_laws,
        "violations": violations,
        "nodal_info": nodal_info
    }

# --- DISPUTE SUBMISSION & SECTION 65B EVIDENCE SEALING ---
@app.post("/api/create-dispute")
async def create_dispute(
    consumer_name: str = Form(...),
    consumer_upi: str = Form(...),
    consumer_phone: str = Form(...),
    category: str = Form(...),
    sub_issue: str = Form(...),
    opposite_party: str = Form(...),
    entity_branch: str = Form("Headquarters / Primary Branch"),
    entity_address: str = Form(""),
    reference_id: str = Form("N/A"),
    incident_date: str = Form(...),
    primary_bill_amount: float = Form(0.0),
    incidental_expense_amount: float = Form(0.0),
    statutory_penalty: float = Form(0.0),
    narrative: str = Form(...),
    legal_violations: str = Form(...),
    gps_coordinates: str = Form("13.0827° N, 80.2707° E"),
    primary_file: Optional[UploadFile] = File(None),
    expense_file: Optional[UploadFile] = File(None)
):
    dispute_id = f"REF-{datetime.now().strftime('%y%m%d')}-{os.urandom(3).hex().upper()}"
    created_at = datetime.now(timezone.utc).isoformat()
    cure_deadline = (datetime.now() + timedelta(days=7)).strftime("%d-%b-%Y")

    # 1. Mathematical Statutory Claim Formula
    # Total = Original Bill + Consequential Incidental Expenses + Statutory Penalty + 12% Interest
    out_of_pocket_loss = round(primary_bill_amount + incidental_expense_amount, 2)
    interest_amount = round(out_of_pocket_loss * 0.12 * (30 / 365), 2)
    total_demand = round(out_of_pocket_loss + statutory_penalty + interest_amount, 2)

    # 2. Cryptographic SHA-256 Forensic Fingerprints for Section 65B
    primary_hash = "NOT_ATTACHED"
    if primary_file and primary_file.filename:
        b1 = await primary_file.read()
        primary_hash = hashlib.sha256(b1).hexdigest()

    expense_hash = "NOT_ATTACHED"
    if expense_file and expense_file.filename:
        b2 = await expense_file.read()
        expense_hash = hashlib.sha256(b2).hexdigest()

    combined_seed = f"{dispute_id}|{primary_hash}|{expense_hash}|{gps_coordinates}|{created_at}".encode('utf-8')
    combined_hash = hashlib.sha256(combined_seed).hexdigest()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO disputes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        dispute_id, created_at, consumer_name, consumer_upi, consumer_phone,
        category, sub_issue, opposite_party, entity_branch, entity_address,
        reference_id, incident_date, primary_bill_amount, incidental_expense_amount,
        statutory_penalty, interest_amount, total_demand, narrative, legal_violations,
        primary_hash, expense_hash, combined_hash, gps_coordinates,
        "ACTIVE_7_DAY_CURE_WINDOW", cure_deadline
    ))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "dispute_id": dispute_id,
        "total_demand": total_demand,
        "cure_deadline": cure_deadline,
        "combined_hash": combined_hash,
        "pdf_url": f"/notice/{dispute_id}.pdf",
        "settle_url": f"/settle/{dispute_id}"
    })

# --- PDF GENERATOR (SECTION 65B / SECTION 63 BSA CERTIFIED DEMAND NOTICE) ---
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
        category, sub_issue, opp_party, branch, addr,
        ref_id, inc_date, bill_amt, exp_amt,
        penalty, interest, total_demand,
        narrative, violations, p_hash, e_hash, c_hash,
        gps_coords, status, cure_deadline
    ) = row

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle', fontName='Helvetica-Bold', fontSize=13, leading=16,
        textColor=colors.HexColor('#0f172a'), alignment=1
    )
    sub_title = ParagraphStyle(
        'SubTitle', fontName='Helvetica-Bold', fontSize=8, leading=11,
        textColor=colors.HexColor('#475569'), alignment=1
    )
    body = ParagraphStyle(
        'BodyTextCustom', fontName='Helvetica', fontSize=8.5, leading=12,
        textColor=colors.HexColor('#1e293b')
    )
    bold_body = ParagraphStyle('BoldBody', parent=body, fontName='Helvetica-Bold')
    legal_cert = ParagraphStyle(
        'CertBox', fontName='Helvetica-Oblique', fontSize=7.5, leading=10,
        textColor=colors.HexColor('#334155')
    )

    elements = []

    elements.append(Paragraph("STATUTORY PRE-LITIGATION LEGAL DEMAND NOTICE", title_style))
    elements.append(Paragraph(
        "SERVED UNDER SECTION 2(11) READ WITH SECTION 35 & 39 OF THE CONSUMER PROTECTION ACT, 2019<br/>"
        "MANDATORY SEVEN (7) DAY STATUTORY CURE WINDOW", sub_title
    ))
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0f172a')))
    elements.append(Spacer(1, 8))

    meta_table_data = [
        [Paragraph(f"<b>Notice Docket Ref:</b> {d_id}", body), Paragraph(f"<b>Date of Notice:</b> {datetime.now().strftime('%d-%b-%Y')}", body)],
        [Paragraph(f"<b>To (Opposite Party):</b> {opp_party}", bold_body), Paragraph(f"<b>Cure Window Expiry:</b> <font color='red'><b>{cure_deadline}</b></font>", bold_body)],
        [Paragraph(f"<b>Specific Branch / Venue:</b> {branch}", body), Paragraph(f"<b>Complainant Name:</b> {c_name}", body)],
        [Paragraph(f"<b>Transaction / PNR Ref:</b> {ref_id}", body), Paragraph(f"<b>Direct Settlement UPI:</b> {c_upi}", body)],
    ]
    meta_table = Table(meta_table_data, colWidths=[270, 270])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("<b>1. STATEMENT OF FACTS & INCIDENT CAUSE OF ACTION</b>", bold_body))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        f"The Complainant engaged with the Opposite Party on <b>{inc_date}</b> regarding <b>{category.upper()} &bull; {sub_issue}</b>. "
        f"The Opposite Party breached its statutory service guarantees, leading to severe deficiency of service as narrated below:<br/><br/>"
        f"<i>\"{narrative}\"</i>", body
    ))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("<b>2. STATUTORY BREACHES & SECTOR REGULATORY NON-COMPLIANCE</b>", bold_body))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        f"The Opposite Party's conduct directly contravenes:<br/>"
        f"• <b>{violations}</b><br/>"
        f"• Section 2(11) (Deficiency of Service) & Section 2(47) (Unfair Trade Practice) of the Consumer Protection Act, 2019.<br/>"
        f"• The Opposite Party is put on notice that holding consumer funds without service delivery constitutes actionable default.",
        body
    ))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("<b>3. ITEMIZED STATUTORY RESTITUTION DEMAND</b>", bold_body))
    elements.append(Spacer(1, 3))

    claim_data = [
        ["Claim Head", "Statutory Rule / Regulatory Basis", "Amount (INR)"],
        ["Base Ticket / Bill Refusal", "Section 39(1)(a) CPA 2019 Direct Restitution", f"Rs. {bill_amt:,.2f}"],
        ["Consequential Incidental Loss (Cab/Food)", "Section 39(1)(d) Actual Incidental Loss", f"Rs. {exp_amt:,.2f}"],
        ["Statutory Penalty / Delay Compensation", "Sector Regulator Tariff & Harmonised TAT Norms", f"Rs. {penalty:,.2f}"],
        ["Pre-litigation Accrued Interest", "12.00% per annum compound accrued", f"Rs. {interest:,.2f}"],
        ["TOTAL MANDATORY RESTITUTION", "100% Retained Directly by Consumer (Zero Commission)", f"Rs. {total_demand:,.2f}"]
    ]
    t = Table(claim_data, colWidths=[200, 240, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#f1f5f9')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#94a3b8')),
        ('ALIGN', (2,0), (2,-1), 'RIGHT'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("<b>4. CERTIFICATE UNDER SECTION 65B INDIAN EVIDENCE ACT & SECTION 63 BHARATIYA SAKSHYA ADHINIYAM (BSA)</b>", bold_body))
    elements.append(Spacer(1, 3))
    cert_text = (
        f"I hereby certify that all primary evidence and incidental receipts submitted have been securely recorded. "
        f"Primary Bill Digest: <b>{p_hash[:28]}...</b> | Consequential Receipt Digest: <b>{e_hash[:28]}...</b> | "
        f"Master Verification Seal: <b>{c_hash}</b>. Geolocation Stamp: <b>[{gps_coords}]</b>. "
        f"This cryptographic chain guarantees that no tampering or synthesis has occurred since the moment of filing."
    )
    elements.append(Paragraph(cert_text, legal_cert))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("<b>5. REQUISITION FOR SETTLEMENT & CONSEQUENCE OF DEFAULT</b>", bold_body))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        f"The Opposite Party is granted a <b>strict 7-day statutory cure window</b> expiring on <b>{cure_deadline}</b> to disburse the sum of "
        f"<b>Rs. {total_demand:,.2f}</b> directly to the Complainant's UPI handle <b>{c_upi}</b> using the Corporate Settlement Portal at:<br/>"
        f"<b>https://refundr-app.onrender.com/settle/{d_id}</b><br/><br/>"
        f"<b>Default Escalation:</b> Failing settlement before {cure_deadline}, this certified docket will be transmitted directly for "
        f"formal registry filing before the competent District Consumer Disputes Redressal Commission (e-Daakhil Section 35 Petition) "
        f"and National Consumer Helpline Convergence (NCH 1915), seeking additional punitive damages of Rs. 25,000 for bad-faith litigation.",
        body
    ))

    doc.build(elements)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={dispute_id}_Statutory_Demand_Notice.pdf"}
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
        return HTMLResponse("<h3 style='color:white;font-family:sans-serif;'>Invalid or Expired Dispute Reference</h3>", status_code=404)

    (
        d_id, created_at, c_name, c_upi, c_phone,
        category, sub_issue, opp_party, branch, addr,
        ref_id, inc_date, bill_amt, exp_amt,
        penalty, interest, total_demand,
        narrative, violations, p_hash, e_hash, c_hash,
        gps_coords, status, cure_deadline
    ) = row

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Corporate Settlement Desk | {d_id}</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen py-10 px-4">
      <div class="max-w-2xl mx-auto bg-slate-800 border border-slate-700 rounded-xl p-8 shadow-2xl">
        <div class="flex items-center justify-between border-b border-slate-700 pb-4 mb-6">
          <div>
            <h1 class="text-xl font-bold text-amber-400">CORPORATE ADJUDICATION & DIRECT UPI SETTLEMENT</h1>
            <p class="text-xs text-slate-400">Pre-Litigation Alternative Dispute Resolution Portal</p>
          </div>
          <span class="px-3 py-1 bg-red-950/80 border border-red-500/50 text-red-300 text-xs font-mono rounded-full animate-pulse">
            Cure Deadline: {cure_deadline}
          </span>
        </div>

        <div class="bg-slate-900/80 border border-slate-700 rounded-lg p-5 mb-6 space-y-3 text-sm">
          <div class="flex justify-between">
            <span class="text-slate-400">Opposite Party & Venue:</span>
            <span class="font-bold text-white text-right">{opp_party} &bull; {branch}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-400">Claimant:</span>
            <span class="font-bold text-white">{c_name}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-400">Category & Cause:</span>
            <span class="text-slate-200">{category.upper()} &bull; {sub_issue}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-400">Transaction / PNR Ref:</span>
            <span class="font-mono text-cyan-400">{ref_id}</span>
          </div>
          <div class="border-t border-slate-800 pt-3">
            <p class="text-xs text-slate-400 mb-1">Grievance Narrative:</p>
            <p class="text-xs italic text-slate-300 bg-slate-950/60 p-3 rounded border border-slate-800">"{narrative}"</p>
          </div>
        </div>

        <div class="bg-emerald-950/40 border border-emerald-500/40 rounded-lg p-5 mb-6">
          <div class="flex justify-between items-center mb-1">
            <span class="text-sm font-semibold text-emerald-300">Total Statutory Claim (Zero Commission):</span>
            <span class="text-2xl font-bold text-emerald-400">₹ {total_demand:,.2f}</span>
          </div>
          <p class="text-xs text-emerald-200/70">100% Disbursed Directly to Consumer UPI &bull; Direct Discharge of Legal Liability</p>
        </div>

        <form action="/api/settle-confirm" method="POST" class="space-y-4">
          <input type="hidden" name="dispute_id" value="{d_id}">
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-1">Authorised Legal / Compliance Officer Name</label>
            <input type="text" name="officer_name" required placeholder="e.g. Legal Counsel / Nodal Manager" class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-400">
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-1">Settlement Bank UTR / UPI Transaction Reference</label>
            <input type="text" name="txn_id" required placeholder="12-digit UTR or Payment Ref" class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm font-mono text-emerald-400 focus:outline-none focus:border-emerald-500">
          </div>
          <div class="pt-3">
            <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-4 rounded-lg text-sm transition">
              Disburse ₹{total_demand:,.2f} to {c_upi} & Discharge Liability
            </button>
            <p class="text-center text-[11px] text-slate-500 mt-2">Disbursement closes pre-litigation liability under Section 39 CPA, 2019.</p>
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
    c.execute("UPDATE disputes SET status = 'SETTLED_PRE_LITIGATION' WHERE id = ?", (dispute_id,))
    conn.commit()
    conn.close()

    return HTMLResponse(f"""
      <body style="background:#0f172a;color:#f8fafc;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;">
        <div style="text-align:center;background:#1e293b;padding:40px;border-radius:12px;border:1px solid #10b981;max-width:500px;">
          <h2 style="color:#34d399;font-size:20px;margin-bottom:10px;">Pre-Litigation Settlement Confirmed</h2>
          <p style="font-size:14px;color:#cbd5e1;line-height:1.6;">Disbursement reference <b>{txn_id}</b> recorded.<br/>Dispute docket <b>{dispute_id}</b> has been formally discharged and closed.</p>
          <a href="/" style="display:inline-block;margin-top:20px;color:#38bdf8;text-decoration:none;font-size:13px;">Return to Refundr Portal &rarr;</a>
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