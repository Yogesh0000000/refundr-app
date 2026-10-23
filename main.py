import os
import json
import uuid
from io import BytesIO
import requests
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

DATABASE_URL = "sqlite:///./refundr.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CaseRecord(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    case_ref = Column(String(50), unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    merchant_name = Column(String(255))
    complaint_summary = Column(Text)
    incident_category = Column(String(255))
    statutory_provision = Column(String(255))
    actual_loss = Column(Float, default=0.0)
    compensation_claimed = Column(Float, default=0.0)
    recommended_demand = Column(Float, default=0.0)
    user_payout = Column(Float, default=0.0)
    platform_fee = Column(Float, default=0.0)
    status = Column(String(50), default="Evidence Verified")
    demand_notice_draft = Column(Text)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Refundr Verifiable Engine",
    description="Evidence-Gated Consumer Dispute Resolution",
    version="2.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

class ComplaintInput(BaseModel):
    user_text: str
    stated_loss: float = Field(default=0.0)
    proof_filename: str | None = Field(default=None)

class LegalAnalysisResult(BaseModel):
    case_ref: str | None = None
    is_valid_claim: bool
    evidence_sufficient: bool = True
    missing_evidence_notes: list[str] = []
    rejection_reason: str | None = None
    merchant_name: str | None = None
    incident_category: str | None = None
    statutory_provision: str | None = None
    actual_loss: float = 0.0
    compensation_claimed: float = 0.0
    recommended_demand: float = 0.0
    user_payout: float = 0.0
    platform_fee: float = 0.0
    demand_notice_draft: str | None = None

@app.get("/")
def serve_ui():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"message": "index.html not found"}

@app.get("/cases")
def list_cases():
    db = SessionLocal()
    try:
        return db.query(CaseRecord).order_by(CaseRecord.id.desc()).all()
    finally:
        db.close()

@app.post("/analyze-complaint", response_model=LegalAnalysisResult)
def analyze_complaint(data: ComplaintInput):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable is not set.")

    prompt = f"""
    You are an evidence-driven legal auditor specializing in the Indian Consumer Protection Act, 2019.
    Analyze this consumer grievance:
    Dispute text: "{data.user_text}"
    User-Stated Monetary Loss: INR {data.stated_loss}
    Uploaded Proof Attachment: "{data.proof_filename or 'No file attached'}"

    Rules:
    1. Legal Viability: Verify if there is a genuine violation under CPA 2019 (Deficiency in Service, Unfair Trade Practice, Overcharging, Defective Product). If frivolous or invalid, set "is_valid_claim" to false and provide "rejection_reason".
    
    2. Evidence Check:
       - If no exact monetary loss was stated (stated_loss is 0 or unmentioned), or if no proof is attached:
         Set "evidence_sufficient" to false.
         Populate "missing_evidence_notes" with the concrete proof required (e.g. ["Official Tax Invoice/Booking Ticket", "Proof of transit expenditure"]).
         Set "demand_notice_draft" to null.
       - NEVER hallucinate or guess loss numbers.

    3. True Compensation Standard:
       - If valid and evidence is sufficient:
         - actual_loss = verified direct financial loss (must match stated verifiable loss).
         - Determine a realistic, legally defensible compensation for mental harassment/inconvenience based on court precedents:
           * Minor procedural delay / small overcharge (< INR 500): Compensation max INR 500 - INR 1,000.
           * Mid-tier service failure (cancelled flights, defective electronics INR 1,000 - 20,000): Compensation max INR 2,000 - INR 3,000.
           * Severe breach: Compensation up to INR 5,000.
         - recommended_demand = actual_loss + compensation_claimed.
         - Draft a formal legal demand notice under the Consumer Protection Act, 2019 citing exact sections, demanding remedy within 15 days.

    Return strictly a JSON object with these keys:
    - is_valid_claim: boolean
    - evidence_sufficient: boolean
    - missing_evidence_notes: list of strings
    - rejection_reason: string or null
    - merchant_name: string or null
    - incident_category: string or null
    - statutory_provision: string or null
    - actual_loss: float
    - compensation_claimed: float
    - demand_notice_draft: string or null
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }

    try:
        res = requests.post(url, json=payload, timeout=60)
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Gemini API request timed out.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=f"Google API Error: {res.text}")

    parsed = json.loads(res.json()["candidates"][0]["content"]["parts"][0]["text"])

    is_valid = parsed.get("is_valid_claim", False)
    evidence_sufficient = parsed.get("evidence_sufficient", False)
    missing_evidence = parsed.get("missing_evidence_notes") or []

    if not is_valid:
        return LegalAnalysisResult(
            is_valid_claim=False,
            evidence_sufficient=False,
            rejection_reason=parsed.get("rejection_reason")
        )

    if not evidence_sufficient or data.stated_loss <= 0 or not data.proof_filename:
        if not data.proof_filename and "Receipt/Booking Invoice" not in missing_evidence:
            missing_evidence.append("Official Invoice, Bill, or Booking Confirmation")
        if data.stated_loss <= 0 and "Disputed Transaction Amount" not in missing_evidence:
            missing_evidence.append("Specific financial loss amount in INR")

        return LegalAnalysisResult(
            is_valid_claim=True,
            evidence_sufficient=False,
            missing_evidence_notes=missing_evidence,
            merchant_name=parsed.get("merchant_name"),
            incident_category=parsed.get("incident_category"),
            statutory_provision=parsed.get("statutory_provision")
        )

    actual_loss = float(data.stated_loss)
    comp = float(parsed.get("compensation_claimed") or 1000.0)
    total_demand = round(actual_loss + comp, 2)
    user_payout = round(total_demand * 0.95, 2)
    platform_fee = round(total_demand * 0.05, 2)
    case_ref = f"REF-{str(uuid.uuid4())[:8].upper()}"

    db = SessionLocal()
    try:
        record = CaseRecord(
            case_ref=case_ref,
            merchant_name=parsed.get("merchant_name") or "Opposite Party",
            complaint_summary=data.user_text,
            incident_category=parsed.get("incident_category") or "Consumer Grievance",
            statutory_provision=parsed.get("statutory_provision") or "Consumer Protection Act, 2019",
            actual_loss=actual_loss,
            compensation_claimed=comp,
            recommended_demand=total_demand,
            user_payout=user_payout,
            platform_fee=platform_fee,
            status="Evidence Verified",
            demand_notice_draft=parsed.get("demand_notice_draft")
        )
        db.add(record)
        db.commit()
    finally:
        db.close()

    return LegalAnalysisResult(
        case_ref=case_ref,
        is_valid_claim=True,
        evidence_sufficient=True,
        merchant_name=parsed.get("merchant_name"),
        incident_category=parsed.get("incident_category"),
        statutory_provision=parsed.get("statutory_provision"),
        actual_loss=actual_loss,
        compensation_claimed=comp,
        recommended_demand=total_demand,
        user_payout=user_payout,
        platform_fee=platform_fee,
        demand_notice_draft=parsed.get("demand_notice_draft")
    )

@app.get("/download-notice/{case_ref}")
def download_notice_pdf(case_ref: str):
    db = SessionLocal()
    try:
        record = db.query(CaseRecord).filter(CaseRecord.case_ref == case_ref).first()
        if not record or not record.demand_notice_draft:
            raise HTTPException(status_code=404, detail="Case or notice draft not found.")

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=45, leftMargin=45, topMargin=45, bottomMargin=45)
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            name='NoticeHeader',
            parent=styles['Heading1'],
            fontSize=14,
            leading=18,
            textColor=colors.HexColor('#0f172a'),
            alignment=1,
            spaceAfter=12
        )
        meta_style = ParagraphStyle(
            name='NoticeMeta',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#64748b'),
            alignment=1,
            spaceAfter=16
        )
        body_style = ParagraphStyle(
            name='NoticeBody',
            parent=styles['Normal'],
            fontSize=10,
            leading=14.5,
            textColor=colors.HexColor('#1e293b')
        )

        elements = [
            Paragraph("VERIFIED LEGAL NOTICE FOR CONSUMER REDRESSAL", title_style),
            Paragraph(f"Under Consumer Protection Act, 2019 | Case Ref: <b>{record.case_ref}</b>", meta_style),
            Spacer(1, 10)
        ]

        for line in record.demand_notice_draft.split("\n"):
            clean = line.strip()
            if clean:
                elements.append(Paragraph(clean, body_style))
                elements.append(Spacer(1, 5))

        doc.build(elements)
        pdf_data = buffer.getvalue()
        buffer.close()

        headers = {"Content-Disposition": f"attachment; filename=Verified_Notice_{record.case_ref}.pdf"}
        return Response(content=pdf_data, media_type="application/pdf", headers=headers)
    finally:
        db.close()