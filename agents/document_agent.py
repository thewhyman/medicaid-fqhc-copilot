"""Document Intelligence Agent: classification, extraction, and validation.

LLM for classification + extraction; deterministic for validation.
Processes documents uploaded during the Medicaid renewal workflow.
"""

import json
import logging
from datetime import date, datetime, timedelta

from openai import OpenAI

from agents.base import AgentResult
from config import DOCUMENT_CONFIDENCE_THRESHOLD, MODEL

logger = logging.getLogger(__name__)


# Document types and their validation rules
DOCUMENT_TYPES = {
    "pay_stub": {
        "description": "Recent pay stub showing earnings",
        "required_fields": ["employer_name", "pay_period_start", "pay_period_end", "gross_pay"],
        "date_field": "pay_period_end",
        "max_age_days": 60,
    },
    "tax_return": {
        "description": "Federal tax return (Form 1040)",
        "required_fields": ["filing_year", "adjusted_gross_income", "filing_status"],
        "date_field": None,
        "max_age_days": None,
    },
    "employer_letter": {
        "description": "Employment verification letter",
        "required_fields": ["employer_name", "employee_name", "salary", "letter_date"],
        "date_field": "letter_date",
        "max_age_days": 90,
    },
    "ssa_benefit_letter": {
        "description": "SSA/SSI/SSDI benefit letter",
        "required_fields": ["monthly_benefit_amount", "benefit_type", "effective_date"],
        "date_field": "effective_date",
        "max_age_days": 365,
    },
    "utility_bill": {
        "description": "Utility bill for residency verification",
        "required_fields": ["service_address", "billing_date", "account_holder_name"],
        "date_field": "billing_date",
        "max_age_days": 90,
    },
    "lease_agreement": {
        "description": "Lease or mortgage document",
        "required_fields": ["property_address", "tenant_name", "lease_start_date"],
        "date_field": None,
        "max_age_days": None,
    },
    "birth_certificate": {
        "description": "Birth certificate for identity/age verification",
        "required_fields": ["full_name", "date_of_birth"],
        "date_field": None,
        "max_age_days": None,
    },
    "immigration_document": {
        "description": "Immigration status document",
        "required_fields": ["document_type", "holder_name", "expiration_date", "status"],
        "date_field": "expiration_date",
        "max_age_days": None,
    },
    "pregnancy_verification": {
        "description": "Pregnancy verification from healthcare provider",
        "required_fields": ["patient_name", "provider_name", "estimated_due_date"],
        "date_field": None,
        "max_age_days": None,
    },
}

CLASSIFY_PROMPT = """You are a document classification agent for a Medicaid renewal system.
Given the text content of a document, classify it into one of these types:
{doc_types}

Respond with ONLY a JSON object:
{{"document_type": "<type>", "confidence": <0.0-1.0>}}

If the document doesn't match any type, use "unknown" as the type with confidence 0.0."""

EXTRACT_PROMPT = """You are a document extraction agent for a Medicaid renewal system.
Extract structured data from this {doc_type} document.

Required fields: {required_fields}

Respond with ONLY a JSON object containing the extracted fields.
Use null for any field you cannot extract. Include a "confidence" field (0.0-1.0)
representing your overall confidence in the extraction accuracy.

Document text:
{document_text}"""


class DocumentAgent:
    """Parse, validate, and verify documents uploaded during renewal."""

    def __init__(self, openai_client: OpenAI):
        self.client = openai_client

    def classify(self, document_text: str) -> AgentResult:
        """LLM classifies document type.

        Args:
            document_text: Raw text content of the document.

        Returns:
            AgentResult with data containing document_type and confidence.
        """
        doc_types_str = "\n".join(
            f"- {key}: {val['description']}" for key, val in DOCUMENT_TYPES.items()
        )
        prompt = CLASSIFY_PROMPT.format(doc_types=doc_types_str)

        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": document_text[:5000]},
                ],
            )
            result_text = response.choices[0].message.content or "{}"
            result = json.loads(result_text)

            return AgentResult(
                success=True,
                data={
                    "document_type": result.get("document_type", "unknown"),
                    "confidence": result.get("confidence", 0.0),
                },
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Document classification failed: %s", e)
            return AgentResult(
                success=False,
                error=f"Classification failed: {e}",
                data={"document_type": "unknown", "confidence": 0.0},
            )

    def extract(self, document_text: str, doc_type: str) -> AgentResult:
        """LLM extracts structured fields from document.

        Args:
            document_text: Raw text content.
            doc_type: Document type from classify().

        Returns:
            AgentResult with data containing extracted fields and confidence.
        """
        doc_config = DOCUMENT_TYPES.get(doc_type)
        if not doc_config:
            return AgentResult(
                success=False,
                error=f"Unknown document type: {doc_type}",
            )

        prompt = EXTRACT_PROMPT.format(
            doc_type=doc_config["description"],
            required_fields=", ".join(doc_config["required_fields"]),
            document_text=document_text[:5000],
        )

        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "Extract the fields from the document above."},
                ],
            )
            result_text = response.choices[0].message.content or "{}"
            extracted = json.loads(result_text)

            return AgentResult(
                success=True,
                data={
                    "extracted_data": extracted,
                    "confidence": extracted.pop("confidence", 0.0),
                },
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Document extraction failed: %s", e)
            return AgentResult(
                success=False,
                error=f"Extraction failed: {e}",
            )

    def validate(self, extracted_data: dict, doc_type: str, patient: dict) -> AgentResult:
        """Deterministic validation: dates within range, names match, required fields present.

        Args:
            extracted_data: Fields extracted by extract().
            doc_type: Document type.
            patient: Patient record for cross-reference.

        Returns:
            AgentResult with data containing status, issues list.
        """
        doc_config = DOCUMENT_TYPES.get(doc_type)
        if not doc_config:
            return AgentResult(
                success=False,
                error=f"Unknown document type: {doc_type}",
            )

        issues = []

        # Check required fields
        for field in doc_config["required_fields"]:
            if not extracted_data.get(field):
                issues.append(f"Missing required field: {field}")

        # Date freshness check
        date_field = doc_config.get("date_field")
        max_age = doc_config.get("max_age_days")
        if date_field and extracted_data.get(date_field):
            try:
                doc_date = _parse_date_flexible(extracted_data[date_field])
                if doc_date:
                    if max_age is not None:
                        age_days = (date.today() - doc_date).days
                        if age_days > max_age:
                            issues.append(
                                f"Document too old: {date_field} is {age_days} days ago "
                                f"(max {max_age} days)"
                            )
                    # Immigration docs: check expiration is in the future
                    if doc_type == "immigration_document" and date_field == "expiration_date":
                        if doc_date < date.today():
                            issues.append("Immigration document has expired")
            except (ValueError, TypeError):
                issues.append(f"Could not parse date field: {date_field}")

        # Tax return: check filing year
        if doc_type == "tax_return" and extracted_data.get("filing_year"):
            current_year = date.today().year
            filing_year = extracted_data["filing_year"]
            if isinstance(filing_year, str):
                try:
                    filing_year = int(filing_year)
                except ValueError:
                    issues.append(f"Invalid filing year: {filing_year}")
                    filing_year = None
            if filing_year and filing_year < current_year - 1:
                issues.append(f"Filing year {filing_year} is too old (need {current_year - 1} or {current_year})")

        # Name cross-reference
        patient_name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip().lower()
        if patient_name:
            name_fields = ["employee_name", "account_holder_name", "tenant_name",
                           "holder_name", "patient_name", "full_name"]
            for nf in name_fields:
                doc_name = extracted_data.get(nf, "")
                if doc_name and isinstance(doc_name, str):
                    if not _names_match(patient_name, doc_name.lower()):
                        issues.append(f"Name mismatch: document has '{doc_name}', patient is '{patient_name}'")
                    break  # Only check first matching name field

        status = "accepted" if not issues else "rejected"
        return AgentResult(
            success=True,
            data={
                "status": status,
                "issues": issues,
            },
        )

    def process(self, document_text: str, patient: dict) -> AgentResult:
        """Full pipeline: classify → extract → validate.

        Args:
            document_text: Raw text content of the uploaded document.
            patient: Patient record for cross-reference.

        Returns:
            AgentResult with data containing status, document_type,
            extracted_data, confidence, issues.
        """
        # Step 1: Classify
        classify_result = self.classify(document_text)
        if not classify_result.success:
            return AgentResult(
                success=False,
                error=f"Classification failed: {classify_result.error}",
                data={"status": "rejected"},
            )

        doc_type = classify_result.data["document_type"]
        classify_confidence = classify_result.data["confidence"]

        if doc_type == "unknown":
            return AgentResult(
                success=True,
                data={
                    "status": "rejected",
                    "document_type": "unknown",
                    "confidence": 0.0,
                    "issues": ["Could not classify document type"],
                    "extracted_data": {},
                },
            )

        # Step 2: Extract
        extract_result = self.extract(document_text, doc_type)
        if not extract_result.success:
            return AgentResult(
                success=True,
                data={
                    "status": "needs_review",
                    "document_type": doc_type,
                    "confidence": classify_confidence,
                    "issues": [f"Extraction failed: {extract_result.error}"],
                    "extracted_data": {},
                },
            )

        extracted_data = extract_result.data.get("extracted_data", {})
        extract_confidence = extract_result.data.get("confidence", 0.0)
        overall_confidence = min(classify_confidence, extract_confidence)

        # Step 3: Validate
        validate_result = self.validate(extracted_data, doc_type, patient)
        validation_status = validate_result.data.get("status", "rejected")
        issues = validate_result.data.get("issues", [])

        # Confidence threshold check
        if overall_confidence < DOCUMENT_CONFIDENCE_THRESHOLD:
            final_status = "needs_review"
            issues.append(
                f"Confidence {overall_confidence:.2f} below threshold "
                f"{DOCUMENT_CONFIDENCE_THRESHOLD}"
            )
        else:
            final_status = validation_status

        return AgentResult(
            success=True,
            data={
                "status": final_status,
                "document_type": doc_type,
                "confidence": round(overall_confidence, 2),
                "issues": issues,
                "extracted_data": extracted_data,
            },
            audit_log_entry={
                "actor": "document_agent",
                "action": "document_processed",
                "details": {
                    "document_type": doc_type,
                    "status": final_status,
                    "confidence": round(overall_confidence, 2),
                    "issue_count": len(issues),
                },
                "phi_accessed": True,
            },
        )


def _parse_date_flexible(value) -> date | None:
    """Parse a date from various formats."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _names_match(name_a: str, name_b: str) -> bool:
    """Check if two names likely refer to the same person.

    Simple check: all parts of the shorter name appear in the longer name.
    """
    parts_a = set(name_a.split())
    parts_b = set(name_b.split())
    shorter, longer = (parts_a, parts_b) if len(parts_a) <= len(parts_b) else (parts_b, parts_a)
    return shorter.issubset(longer)
