"""Kør mod rigtige SharePoint-skabeloner efter pladsholderne er rettet."""
import configuration as cfg
from document_service import create_docx_and_pdf
from sharepoint_gateway import SharePointGateway

VALUES = {
    "DATO": "28. oktober 2026",
    "SAGSTYPE_OG_DATO": "Fleksjobsag - 01-01-2026",
    "NAVN": "Test Testesen",
    "CPR": "0101010101",
    "INSTITUTIONSNAVN": "Testmyndighed",
    "FLEKSJOB": cfg.FLEXJOB_AUTHORITY_TEXT,
}

def run_one(template_name, required, docx_name, pdf_name):
    template = SharePointGateway().get_template(template_name)
    docx, pdf = create_docx_and_pdf(template_bytes=template, values=VALUES, required_fields=required, docx_file_name=docx_name, pdf_file_name=pdf_name)
    print(template_name, len(docx.content), len(pdf.content))

if __name__ == "__main__":
    run_one(cfg.CITIZEN_CONSENT_TEMPLATE_FILE_NAME, cfg.CITIZEN_CONSENT_PLACEHOLDERS, cfg.CITIZEN_CONSENT_DOCX_FILE_NAME, cfg.CITIZEN_CONSENT_PDF_FILE_NAME)
    run_one(cfg.AUTHORITY_REQUEST_TEMPLATE_FILE_NAME, cfg.AUTHORITY_REQUEST_PLACEHOLDERS, cfg.AUTHORITY_REQUEST_DOCX_FILE_NAME, cfg.AUTHORITY_REQUEST_PDF_FILE_NAME)
    run_one(cfg.AUTHORITY_REMINDER_TEMPLATE_FILE_NAME, cfg.AUTHORITY_REMINDER_PLACEHOLDERS, cfg.AUTHORITY_REMINDER_DOCX_FILE_NAME, cfg.AUTHORITY_REMINDER_PDF_FILE_NAME)

