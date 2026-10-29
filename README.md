# Anmod om aktindsigt hos andre

Alle procesfiler og testfiler ligger i repositoryets rod. Strukturen følger processkabelonen med `main.py`, `behandel.py`, `configuration.py` og `.env` i roden.

## Kørsel

- `python main.py --queue` kører producer-delen.
- `python main.py` kører worker-delen.
- `python main.py --debug` kører worker-delen i debugtilstand.
- `pytest` finder `test_*.py` direkte i roden.
- `python template_test.py` tester Word-skabelonen fra SharePoint og PDF-konverteringen.

