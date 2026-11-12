"""Generate SYNTHETIC sample documents for testing upload + extraction.

    python -m scripts.make_sample_docs        # writes ./sample_documents/

Every document is fictional and stamped "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT". The Aadhaar-style
numbers start with 0, which no real Aadhaar number does.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "sample_documents"

DOCS: dict[str, tuple[str, list[str]]] = {
    "aadhaar_sunita.pdf": ("Unique Identification Authority of India (SAMPLE)", [
        "Name: Sunita Devi", "Date of Birth: 12/03/1958", "Gender: Female",
        "Address: Village Rampur, District Gaya, Bihar", "Aadhaar No: 0123 4567 8901"]),
    "income_certificate_sunita.pdf": ("Income Certificate - Revenue Department, Government of Bihar (SAMPLE)", [
        "Certificate No: BR-INC-2026-000123", "Name of Applicant: Sunita Devi", "District: Gaya",
        "State: Bihar", "Annual Family Income: Rs. 60,000", "Date of Issue: 02/09/2026"]),
    "ration_card_sunita.pdf": ("Ration Card - Food & Civil Supplies, Bihar (SAMPLE)", [
        "Card Type: Antyodaya Anna Yojana (AAY)", "Head of Family Name: Sunita Devi",
        "Number of Members: 3", "State: Bihar"]),
    "caste_certificate_ramesh.pdf": ("Caste Certificate - Government of Uttar Pradesh (SAMPLE)", [
        "Name: Ramesh Kumar", "This is to certify that the applicant belongs to the Scheduled Caste category.",
        "District: Lucknow", "State: Uttar Pradesh", "Date of Issue: 15/01/2026"]),
    "land_record_ramesh.pdf": ("Khatauni (Record of Rights) - Uttar Pradesh (SAMPLE)", [
        "Name of Holder: Ramesh Kumar", "Khasra No: 112, 113", "Total Area: 1.2 hectares",
        "District: Barabanki", "State: Uttar Pradesh"]),
    "bank_passbook_ramesh.pdf": ("Savings Account Passbook - Sample Gramin Bank (SAMPLE)", [
        "Account Holder Name: Ramesh Kumar", "A/c No: 001234567890", "IFSC: SAMP0001234",
        "Savings Account"]),
    "marksheet_priya.pdf": ("Board of Secondary Education - Class XII Marksheet (SAMPLE)", [
        "Name of Candidate: Priya Sharma", "Date of Birth: 05/08/2007", "Class 12 (Higher Secondary)",
        "Marks Obtained: 452 out of 500", "Percentage: 90.4 %", "Result: PASS"]),
    "disability_certificate_ramesh.pdf": ("Certificate of Disability - Chief Medical Officer (SAMPLE)", [
        "Name: Ramesh Kumar", "Type of disability: Locomotor", "Percentage of permanent disability: 45 %",
        "District: Lucknow"]),
}


def make_pdf(path: Path, title: str, lines: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(30, 30, 565, 812), color=(0.06, 0.3, 0.2), width=2)
    page.insert_text((50, 80), title, fontsize=13, fontname="helv", color=(0.06, 0.3, 0.2))
    page.insert_text((50, 105), "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT", fontsize=10, color=(0.8, 0.1, 0.1))
    y = 150
    for line in lines:
        page.insert_text((50, y), line, fontsize=12, fontname="helv")
        y += 28
    doc.save(path)
    doc.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, (title, lines) in DOCS.items():
        make_pdf(OUT / name, title, lines)
    # One image version, to exercise the OCR path (needs the tesseract binary).
    with pymupdf.open(OUT / "aadhaar_sunita.pdf") as d:
        d[0].get_pixmap(dpi=150).save(OUT / "aadhaar_sunita_scan.png")
    with pymupdf.open(OUT / "income_certificate_sunita.pdf") as d:
        d[0].get_pixmap(dpi=150).save(OUT / "income_certificate_sunita_scan.png")
    print(f"wrote {len(DOCS) + 2} synthetic files to {OUT}")


if __name__ == "__main__":
    main()
