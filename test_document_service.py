from datetime import date
import pytest
from document_service import dansk_dato, validate_template_values

def test_dansk_dato():
    assert dansk_dato(date(2026, 10, 19)) == "19. oktober 2026"

def test_manglende_placeholder_vaerdi():
    with pytest.raises(ValueError, match="INSTITUTIONSNAVN"):
        validate_template_values(("SAGSNUMMER", "INSTITUTIONSNAVN"), {"SAGSNUMMER": "1"})
