import csv
import io
import phonenumbers
from app.models import Lead

class LeadService:
    def __init__(self, db):
        self.db = db

    def ingest_csv(self, file_content: bytes) -> dict:
        text = file_content.decode("utf-8-sig")  # handle Excel BOM
        reader = csv.DictReader(io.StringIO(text))
        results = {"created": 0, "updated": 0, "failed": 0, "errors": []}

        for row_num, row in enumerate(reader, start=2):
            try:
                phone = self._normalize_phone(row.get("phone", ""))
                email = row["email"].strip().lower()

                # Upsert: update if exists, insert if new
                existing = self.db.query(Lead).filter_by(email=email).first()
                if existing:
                    existing.name = row["name"].strip()
                    existing.phone = phone
                    existing.language = row.get("language", "english").strip()
                    existing.notes = row.get("notes", "")
                    results["updated"] += 1
                else:
                    lead = Lead(
                        name=row["name"].strip(),
                        company=row.get("company", "").strip(),
                        email=email,
                        phone=phone,
                        language=row.get("language", "english").strip(),
                        notes=row.get("notes", ""),
                    )
                    self.db.add(lead)
                    results["created"] += 1

                self.db.commit()
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"row": row_num, "error": str(e)})
                self.db.rollback()

        return results

    def _normalize_phone(self, raw: str) -> str:
        """Normalize to E.164: +91XXXXXXXXXX"""
        raw = raw.strip().replace(" ", "").replace("-", "")
        if not raw:
            raise ValueError("Phone number is required")
        if not raw.startswith("+"):
            raw = "+91" + raw
        try:
            parsed = phonenumbers.parse(raw, None)
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError(f"Invalid number: {raw}")
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            raise ValueError(f"Cannot parse phone: {raw}")