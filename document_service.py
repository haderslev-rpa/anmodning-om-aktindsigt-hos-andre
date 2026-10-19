"""Udfylder Word-skabeloner og konverterer resultatet til PDF."""
from datetime import date
from q_gotenberg.fil_til_pdf import konverter_fil_til_pdf
from q_word.udfyld_skabelon import udfyld_word_skabelon
from models import BinaryFile

DANISH_MONTHS = ("januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december")

def dansk_dato(value: date) -> str:
    return f"{value.day}. {DANISH_MONTHS[value.month - 1]} {value.year}"

def validate_template_values(required_fields: tuple[str, ...], values: dict[str, object]) -> None:
    missing = [name for name in required_fields if name not in values]
    if missing:
        raise ValueError("Mangler værdier til Word-pladsholdere: " + ", ".join(missing))

def create_docx_and_pdf(*, template_bytes: bytes, values: dict[str, object], required_fields: tuple[str, ...], docx_file_name: str, pdf_file_name: str) -> tuple[BinaryFile, BinaryFile]:
    validate_template_values(required_fields, values)
    docx_bytes = udfyld_word_skabelon(skabelon_bytes=template_bytes, vaerdier=values)
    pdf_bytes = konverter_fil_til_pdf(filnavn=docx_file_name, filindhold=docx_bytes)
    return (
        BinaryFile(docx_file_name, docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        BinaryFile(pdf_file_name, pdf_bytes, "application/pdf"),
    )
