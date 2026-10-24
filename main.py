import os
import io
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
import requests
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

DATABASE_URL = "sqlite:///./refundr.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

NCH_CONVERGENCE_PARTNERS = [
    "indigo", "air india", "spicejet", "vistara", "irctc", 
    "amazon", "flipkart", "myntra", "swiggy", "zomato", 
    "airtel", "jio", "vodafone idea", "tata play", "makemytrip"
]

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    phone = Column(String(20), nullable=False)
    pincode = Column(String(10), nullable=False)
    upi_id = Column(String(100), default="unlinked@upi")
    created_at = Column(DateTime, default=datetime.utcnow)
    claims = relationship("DisputeRecord", back_populates="claimant")

class DisputeRecord(Base):
    __tablename__ = "disputes"
    id = Column(Integer, primary_key=True, index=True)
    case_ref = Column(String(64), unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    respondent_name = Column(String(150), nullable=False)
    category = Column(String(60), nullable=False)
    incident_date = Column(String(30), nullable=False)
    is_nch_partner = Column(Boolean, default=False)
    dark_pattern_flag = Column(Boolean, default=False)
    dark_pattern_type = Column(String(100), nullable=True)
    jurisdiction_forum = Column(String(150))
    base_principal = Column(Float, default=0.0)
    statutory_relief = Column(Float, default=0.0)
    accrued_interest = Column(Float, default=0.0)
    legal_costs = Column(Float, default=1500.0)
    total_claim = Column(Float, default=0.0)
    user_recovery_share = Column(Float, default=0.0)
    platform_fee_share = Column(Float, default=0.0)
    evidence_hash = Column(String(64), nullable=True)
    sec65b_certificate = Column(Text, nullable=True)
    gps_telemetry = Column(Text, nullable=True)
    network_telemetry = Column(Text, nullable=True)
    list_of_dates = Column(Text, nullable=True)
    legal_grounds_text = Column(Text, nullable=True)
    stage = Column(String(50), default="STAGE_1_NOTICE")
    dispatched_at = Column(DateTime, default=datetime.utcnow)
    settlement_status = Column(String(40), default="7-Day Cure Window Running")
    
    # Corporate Apology & Admission Channel
    corporate_apology_text = Column(Text, nullable=True)
    apology_officer_title = Column(String(150), nullable=True)
    apology_submitted_at = Column(DateTime, nullable=True)

    claimant = relationship("User", back_populates="claims")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Refundr Legal Escalation Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class RegisterSchema(BaseModel):
    full_name: str
    email: str
    phone: str
    pincode: str
    upi_id: Optional[str] = "unlinked@upi"

class DisputeCreateSchema(BaseModel):
    user_email: str
    respondent_name: str
    category: str
    incident_date: str
    invoice_amount: float
    narrative: str
    dark_pattern: bool = False
    dark_pattern_type: Optional[str] = None
    gps_log: Optional[str] = None
    network_log: Optional[str] = None

class CorporateSettleSchema(BaseModel):
    apology_text: Optional[str] = None
    officer_title: Optional[str] = "Designated Grievance Officer"

def calculate_statutory_relief(category: str, invoice_val: float) -> tuple[float, str]:
    cat = category.lower()
    if "flight" in cat or "aviation" in cat:
        return 7500.0, "DGCA CAR Section 3, Series M, Part IV (Statutory Delay Compensation)"
    elif "rail" in cat or "train" in cat:
        return round(invoice_val * 1.0, 2), "IRCTC / Railway TDR Delay & Refund Rules"
    elif "telecom" in cat or "broadband" in cat:
        return 1200.0, "TRAI Quality of Service (QoS) Regulations & Outage Credit Rebate"
    elif "e-commerce" in cat or "retail" in cat:
        return 1000.0, "Consumer Protection (E-Commerce) Rules, 2020 Statutory Breach"
    return 1500.0, "Consumer Protection Act, 2019 Deficiency Restitution"

def calculate_penal_interest(principal: float, incident_date_str: str) -> float:
    try:
        inc_dt = datetime.strptime(incident_date_str, "%Y-%m-%d")
        days_passed = max(1, (datetime.utcnow() - inc_dt).days)
        return round(principal * (0.12 / 365) * days_passed, 2)
    except Exception:
        return 0.0

@app.get("/")
def serve_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"status": "Refundr Engine Online."}

@app.post("/api/register")
def register_user(payload: RegisterSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.strip()).first()
    if not user:
        user = User(
            full_name=payload.full_name.strip(),
            email=payload.email.strip(),
            phone=payload.phone.strip(),
            pincode=payload.pincode.strip(),
            upi_id=payload.upi_id.strip() if payload.upi_id else "unlinked@upi"
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "phone": user.phone,
        "pincode": user.pincode,
        "upi_id": user.upi_id
    }

@app.post("/api/audit-and-file")
def audit_and_file(payload: DisputeCreateSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.user_email.strip()).first()
    if not user:
        raise HTTPException(status_code=400, detail="User account not found. Please register first.")

    case_ref = f"REF-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    base_val = max(100.0, float(payload.invoice_amount))
    statutory_amt, reg_title = calculate_statutory_relief(payload.category, base_val)
    interest_amt = calculate_penal_interest(base_val, payload.incident_date)
    legal_expense = 1500.0
    is_conv = any(p in payload.respondent_name.lower() for p in NCH_CONVERGENCE_PARTNERS)
    
    total_demand = round(base_val + statutory_amt + interest_amt + legal_expense, 2)
    user_payout = round(total_demand * 0.95, 2)
    platform_fee = round(total_demand * 0.05, 2)
    jurisdiction = f"District Consumer Commission (Territorial Pincode: {user.pincode})"

    raw_evidence = f"{case_ref}|{user.email}|{payload.respondent_name}|{base_val}|{payload.gps_log}|{payload.network_log}"
    sha256_hash = hashlib.sha256(raw_evidence.encode('utf-8')).hexdigest()
    sec65b_cert = (
        f"CERTIFICATE UNDER SECTION 65B / SECTION 63 BSA:\n"
        f"Cryptographic Hash: {sha256_hash}\n"
        f"Status: Immutable Electronic Log | Timestamp: {datetime.utcnow().isoformat()}Z"
    )

    list_of_dates = (
        f"1. {payload.incident_date}: Transaction & deficiency by {payload.respondent_name}.\n"
        f"2. {datetime.utcnow().strftime('%Y-%m-%d')}: Statutory Legal Notice served via Refundr.\n"
        f"3. Accrued Penal Interest: 12% p.a. calculated from date of default."
    )

    legal_grounds = (
        f"Actionable deficiency of service established under Section 2(11) of the Consumer Protection Act, 2019 "
        f"against {payload.respondent_name}. "
    )
    if payload.dark_pattern:
        legal_grounds += (
            f"Additionally, the Opposite Party engaged in prohibited Dark Pattern practices ({payload.dark_pattern_type}) "
            f"in direct contravention of the CCPA Guidelines and Section 2(47) Unfair Trade Practices. "
        )
    legal_grounds += (
        f"Principal restitution of INR {base_val:,.2f}, statutory relief under {reg_title} of INR {statutory_amt:,.2f}, "
        f"pre-litigation interest of INR {interest_amt:,.2f}, and drafting costs of INR {legal_expense:,.2f} demanded."
    )

    dispute = DisputeRecord(
        case_ref=case_ref,
        user_id=user.id,
        respondent_name=payload.respondent_name,
        category=payload.category,
        incident_date=payload.incident_date,
        is_nch_partner=is_conv,
        dark_pattern_flag=payload.dark_pattern,
        dark_pattern_type=payload.dark_pattern_type,
        jurisdiction_forum=jurisdiction,
        base_principal=base_val,
        statutory_relief=statutory_amt,
        accrued_interest=interest_amt,
        legal_costs=legal_expense,
        total_claim=total_demand,
        user_recovery_share=user_payout,
        platform_fee_share=platform_fee,
        evidence_hash=sha256_hash,
        sec65b_certificate=sec65b_cert,
        gps_telemetry=payload.gps_log,
        network_telemetry=payload.network_log,
        list_of_dates=list_of_dates,
        legal_grounds_text=legal_grounds,
        stage="STAGE_1_NOTICE",
        settlement_status="7-Day Cure Window Running"
    )
    db.add(dispute)
    db.commit()
    db.refresh(dispute)

    return {
        "case_ref": dispute.case_ref,
        "stage": dispute.stage,
        "is_nch_partner": dispute.is_nch_partner,
        "total_claim": dispute.total_claim,
        "interest": dispute.accrued_interest,
        "user_recovery": dispute.user_recovery_share,
        "platform_fee": dispute.platform_fee_share,
        "settlement_portal": f"/settle/{dispute.case_ref}"
    }

@app.get("/api/dashboard/{email}")
def get_dashboard(email: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email.strip()).first()
    if not user:
        return {"total_recovered": 0, "active_disputes": 0, "claims": []}

    disputes = db.query(DisputeRecord).filter(DisputeRecord.user_id == user.id).all()
    total_recovered = sum([d.user_recovery_share for d in disputes if d.settlement_status == "Settled & Disbursed"])
    active_count = len([d for d in disputes if d.settlement_status != "Settled & Disbursed"])

    claim_list = []
    for d in disputes:
        elapsed = (datetime.utcnow() - d.dispatched_at).days
        days_left = max(0, 7 - elapsed)
        claim_list.append({
            "case_ref": d.case_ref,
            "respondent": d.respondent_name,
            "category": d.category,
            "is_nch_partner": d.is_nch_partner,
            "dark_pattern": d.dark_pattern_flag,
            "interest": d.accrued_interest,
            "total_claim": d.total_claim,
            "user_payout": d.user_recovery_share,
            "stage": d.stage,
            "status": d.settlement_status,
            "days_left": days_left,
            "apology_text": d.corporate_apology_text,
            "apology_officer": d.apology_officer_title,
            "pdf_url": f"/api/notice-pdf/{d.case_ref}",
            "settle_link": f"/settle/{d.case_ref}"
        })

    return {
        "user_name": user.full_name,
        "upi_id": user.upi_id,
        "total_recovered": total_recovered,
        "active_disputes": active_count,
        "claims": claim_list
    }

# Webhook with Corporate Apology & Explanation Module
@app.get("/settle/{case_ref}")
def serve_settlement_portal(case_ref: str, db: Session = Depends(get_db)):
    rec = db.query(DisputeRecord).filter(DisputeRecord.case_ref == case_ref).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Dispute docket not found.")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Refundr Corporate Settlement & Apology Portal</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 flex items-center justify-center min-h-screen p-4 font-sans">
        <div class="max-w-lg w-full bg-slate-900 border border-slate-800 p-6 rounded-2xl space-y-4">
            <div class="border-b border-slate-800 pb-3">
                <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300">{rec.case_ref}</span>
                <h2 class="text-base font-bold text-white mt-1">Pre-Litigation Settlement & Apology Desk</h2>
                <p class="text-xs text-slate-400">Respondent Entity: <b>{rec.respondent_name}</b></p>
            </div>
            
            <div class="space-y-2 text-xs bg-slate-950 p-4 rounded-xl border border-slate-800 font-mono">
                <div class="flex justify-between"><span>Base Restitution:</span><span>INR {rec.base_principal:,.2f}</span></div>
                <div class="flex justify-between"><span>Statutory Penalty:</span><span>INR {rec.statutory_relief:,.2f}</span></div>
                <div class="flex justify-between text-amber-400"><span>12% Accrued Interest:</span><span>INR {rec.accrued_interest:,.2f}</span></div>
                <div class="flex justify-between text-sm font-bold text-emerald-400 border-t border-slate-800 pt-1">
                    <span>Total Remittance:</span><span>INR {rec.total_claim:,.2f}</span>
                </div>
            </div>

            <div class="space-y-2 text-xs">
                <label class="font-bold text-slate-300">Official Apology & Corrective Statement</label>
                <select id="apology-template" onchange="fillTemplate()" class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-300 text-xs">
                    <option value="">-- Choose Corporate Template --</option>
                    <option value="operational">Operational Disruption & Sincere Apology</option>
                    <option value="billing">Billing Error Rectification & Procedural Regret</option>
                    <option value="staff">Customer Experience & Conduct Remediation</option>
                </select>
                <textarea id="apology-body" rows="3" placeholder="Enter official executive apology statement acknowledging service deficiency..." class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 text-xs"></textarea>
                <input id="officer-title" type="text" placeholder="Designation (e.g. Nodal Grievance Officer, Customer Relations)" class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 text-xs">
            </div>

            <div class="text-[11px] text-slate-400 bg-slate-950 p-3 rounded-xl border border-slate-800">
                Settlement Destination: Complainant UPI <b class="text-slate-200">{rec.claimant.upi_id}</b> (95%)
            </div>

            {f'<button onclick="submitCorporateSettlement()" id="settle-btn" class="w-full py-3 bg-emerald-600 hover:bg-emerald-500 font-bold rounded-xl text-xs transition text-white">Approve Full Settlement & Issue Formal Apology</button>' if rec.settlement_status != 'Settled & Disbursed' else '<div class="p-3 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-center rounded-xl text-xs font-bold">✓ Claim Settled, Apology Delivered & Payout Disbursed</div>'}
        </div>

        <script>
            function fillTemplate() {{
                const val = document.getElementById("apology-template").value;
                const txt = document.getElementById("apology-body");
                if (val === "operational") {{
                    txt.value = "We sincerely regret the unforeseen service delay and operational disruption experienced by you. Internal standard operating procedures have been revised to prevent recurrence.";
                }} else if (val === "billing") {{
                    txt.value = "We acknowledge the discrepancy in billing and the delay in processing your return. Immediate systemic corrections have been instituted across our billing gateway.";
                }} else if (val === "staff") {{
                    txt.value = "We unreservedly apologize for the deficient customer support experience encountered. The issue has been escalated to executive management for necessary personnel remediation.";
                }}
            }}

            async function submitCorporateSettlement() {{
                const btn = document.getElementById("settle-btn");
                btn.disabled = true;
                btn.innerText = "Dispatching Settlement & Stamping Apology...";

                const payload = {{
                    apology_text: document.getElementById("apology-body").value || "We tender our sincere apology for the service deficiency and confirm full restitution.",
                    officer_title: document.getElementById("officer-title").value || "Designated Corporate Grievance Officer"
                }};

                const res = await fetch("/api/corporate-settle/{rec.case_ref}", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify(payload)
                }});

                if (res.ok) location.reload();
            }}
        </script>
    </body>
    </html>
    """
    return Response(content=html, media_type="text/html")

@app.post("/api/corporate-settle/{case_ref}")
def corporate_settle_webhook(case_ref: str, payload: CorporateSettleSchema, db: Session = Depends(get_db)):
    rec = db.query(DisputeRecord).filter(DisputeRecord.case_ref == case_ref).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Docket not found")
    rec.settlement_status = "Settled & Disbursed"
    rec.corporate_apology_text = payload.apology_text
    rec.apology_officer_title = payload.officer_title
    rec.apology_submitted_at = datetime.utcnow()
    db.commit()
    return {"status": "Success", "payout_sent": rec.user_recovery_share, "fee_collected": rec.platform_fee_share}

@app.post("/api/escalate-nch/{case_ref}")
def escalate_nch(case_ref: str, db: Session = Depends(get_db)):
    rec = db.query(DisputeRecord).filter(DisputeRecord.case_ref == case_ref).first()
    rec.stage = "STAGE_2_NCH"
    rec.settlement_status = "Escalated to National Consumer Helpline (NCH 1915)"
    db.commit()
    return {"status": "Escalated to NCH"}

@app.post("/api/escalate-court/{case_ref}")
def escalate_court(case_ref: str, db: Session = Depends(get_db)):
    rec = db.query(DisputeRecord).filter(DisputeRecord.case_ref == case_ref).first()
    rec.stage = "STAGE_3_COURT"
    rec.settlement_status = "e-Daakhil Section 35 Petition Ready"
    db.commit()
    return {"status": "Escalated to Court"}

@app.get("/api/notice-pdf/{case_ref}")
def generate_pdf(case_ref: str, db: Session = Depends(get_db)):
    rec = db.query(DisputeRecord).filter(DisputeRecord.case_ref == case_ref).first()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()

    title_s = ParagraphStyle('T', parent=styles['Heading1'], fontSize=15, leading=18, textColor=colors.HexColor("#0f172a"))
    sub_s = ParagraphStyle('S', parent=styles['Normal'], fontSize=8, leading=11, textColor=colors.HexColor("#475569"))
    body_s = ParagraphStyle('B', parent=styles['Normal'], fontSize=9, leading=13, textColor=colors.HexColor("#1e293b"))
    bold_s = ParagraphStyle('BD', parent=styles['Normal'], fontSize=9, leading=13, fontName="Helvetica-Bold", textColor=colors.HexColor("#0f172a"))

    story = [
        Paragraph("FORMAL STATUTORY LEGAL DEMAND NOTICE", title_s),
        Paragraph(f"Ref: {rec.case_ref} | Section 2(11) & Section 2(47) CPA 2019", sub_s),
        Spacer(1, 10)
    ]

    table_data = [
        [Paragraph("<b>Complainant:</b>", body_s), Paragraph(rec.claimant.full_name, body_s)],
        [Paragraph("<b>Opposite Party:</b>", body_s), Paragraph(rec.respondent_name, body_s)],
        [Paragraph("<b>NCH Convergence:</b>", body_s), Paragraph("Official NCH Convergence Partner" if rec.is_nch_partner else "Standard Corporate Desk", bold_s)],
        [Paragraph("<b>Principal Loss:</b>", body_s), Paragraph(f"INR {rec.base_principal:,.2f}", body_s)],
        [Paragraph("<b>Statutory Relief:</b>", body_s), Paragraph(f"INR {rec.statutory_relief:,.2f}", body_s)],
        [Paragraph("<b>Accrued Interest (12% p.a.):</b>", body_s), Paragraph(f"INR {rec.accrued_interest:,.2f}", body_s)],
        [Paragraph("<b>Notice Drafting Costs:</b>", body_s), Paragraph(f"INR {rec.legal_costs:,.2f}", body_s)],
        [Paragraph("<b>TOTAL RECOVERY DEMAND:</b>", bold_s), Paragraph(f"INR {rec.total_claim:,.2f}", bold_s)],
        [Paragraph("<b>SHA-256 Digest:</b>", body_s), Paragraph(rec.evidence_hash, sub_s)]
    ]
    t = Table(table_data, colWidths=[160, 380])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    story.append(Paragraph("<b>LEGAL GROUNDS & VIOLATIONS:</b>", bold_s))
    story.append(Spacer(1, 4))
    story.append(Paragraph(rec.legal_grounds_text.replace("\n", "<br/>"), body_s))
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>SECTION 65B EVIDENCE INTEGRITY CERTIFICATE:</b>", bold_s))
    story.append(Spacer(1, 4))
    story.append(Paragraph(rec.sec65b_certificate.replace("\n", "<br/>"), sub_s))
    story.append(Spacer(1, 10))

    doc.build(story)
    buffer.seek(0)
    return Response(content=buffer.getvalue(), media_type="application/pdf", headers={'Content-Disposition': f'attachment; filename={rec.case_ref}.pdf'})