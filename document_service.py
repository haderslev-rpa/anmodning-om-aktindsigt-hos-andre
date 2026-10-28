"""Udfyld Word og konverter dokumenter/billeder med eksisterende biblioteker."""
from datetime import date
from q_gotenberg.fil_til_pdf import konverter_fil_til_pdf
from q_word.udfyld_skabelon import udfyld_word_skabelon
from models import BinaryFile

MONTHS=("januar","februar","marts","april","maj","juni","juli","august","september","oktober","november","december")

def dansk_dato(value: date) -> str:
    return f"{value.day}. {MONTHS[value.month-1]} {value.year}"

def validate_template_values(required_fields, values):
    missing=[name for name in required_fields if name not in values]
    if missing:
        raise ValueError("Mangler Word-værdier: "+", ".join(missing))

def create_docx_and_pdf(*, template_bytes, values, required_fields, docx_file_name, pdf_file_name):
    validate_template_values(required_fields, values)
    docx=udfyld_word_skabelon(skabelon_bytes=template_bytes, vaerdier=values)
    pdf=konverter_fil_til_pdf(filnavn=docx_file_name, filindhold=docx)
    return BinaryFile(docx_file_name,docx,"application/vnd.openxmlformats-officedocument.wordprocessingml.document"), BinaryFile(pdf_file_name,pdf,"application/pdf")

def image_to_pdf(image_file: BinaryFile, pdf_file_name: str) -> BinaryFile:
    pdf=konverter_fil_til_pdf(filnavn=image_file.file_name, filindhold=image_file.content)
    return BinaryFile(pdf_file_name,pdf,"application/pdf")
