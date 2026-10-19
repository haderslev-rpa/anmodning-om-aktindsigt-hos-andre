"""Manuel test af SharePoint-skabelon, q-word og q-gotenberg."""
import configuration as cfg
from document_service import create_docx_and_pdf
from sharepoint_gateway import SharePointGateway

TEST_VALUES = {"DATO": "19. oktober 2026", "SAGSNUMMER": "TEST-2026-001", "INSTITUTIONSNAVN": "Testmyndighed"}

def main() -> None:
    gateway = SharePointGateway()
    template = gateway.get_template(file_name=cfg.BORGERBREV_TEMPLATE_FILE_NAME)
    docx, pdf = create_docx_and_pdf(
        template_bytes=template,
        values=TEST_VALUES,
        required_fields=cfg.BORGERBREV_PLACEHOLDERS,
        docx_file_name=cfg.BORGERBREV_DOCX_FILE_NAME,
        pdf_file_name=cfg.BORGERBREV_PDF_FILE_NAME,
    )
    print(f"Word-skabelon OK: {len(docx.content)} bytes")
    print(f"PDF-konvertering OK: {len(pdf.content)} bytes")

if __name__ == "__main__":
    main()
