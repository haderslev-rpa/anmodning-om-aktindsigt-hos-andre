from datetime import date
from unittest.mock import patch

from document_service import (
    dansk_dato,
    image_to_pdf,
    parse_case_type_and_date,
)
from models import BinaryFile


def test_dansk_dato():
    assert dansk_dato(date(2026, 10, 28)) == "28. oktober 2026"


def test_parse_case_type_and_date():
    case_type, case_date = parse_case_type_and_date(
        "Fleksjob - 01-01-2027"
    )
    assert case_type == "Fleksjob"
    assert case_date == date(2027, 1, 1)


def test_image_to_pdf_uses_q_gotenberg_public_function():
    source = BinaryFile(
        file_name="samtykke.jpg",
        content=b"image-bytes",
        content_type="image/jpeg",
    )
    with patch(
        "document_service.konverter_fil_til_pdf",
        return_value=b"%PDF-1.7\nTest",
    ) as convert:
        result = image_to_pdf(
            source,
            "Samtykke mundtligt-skriftligt.pdf",
        )

    convert.assert_called_once_with(
        filnavn="samtykke.jpg",
        filindhold=b"image-bytes",
    )
    assert result.content.startswith(b"%PDF-")
