# Krav til q-fasit

Borgerbrevet er samtykkebrevet og sendes via Signeringsportalen. Myndighedsbrevet er anmodningen om sagsakter og sendes via Fasit til modparten, som findes via P-nummer.

q-fasit skal levere genbrugelige, asynkrone funktioner til:

1. `hent_virksomhed_eller_myndighed_via_p_nummer(api_client, *, p_nummer)`.
2. `send_brev_til_virksomhed_eller_myndighed(api_client, *, citizen_id, case_id, p_nummer, institutionsnavn, brevtype, brev, bilag)`.
3. `gem_dokument_paa_sag(api_client, *, citizen_id, case_id, file_name, content, content_type, title, document_category)`.
4. `hent_forsendelsesstatus(api_client, *, forsendelses_id)`, hvis Fasit har et statusendpoint.
5. Brug eksisterende `opret_borgeropgave()` til den afsluttende opgave.

`brev` og hvert bilag skal bruge formen:

```python
{
    "file_name": "Anmodning om aktindsigt.pdf",
    "content": b"%PDF-...",
    "content_type": "application/pdf",
}
```

Krav:

- P-nummer bruges til modtageropslag, men indsættes ikke i brevets indhold.
- Samtykke vedhæftes myndighedsbrevet.
- Digitalt samtykke er `Samtykke modtaget digital.pdf`.
- Mundtligt/skriftligt samtykke kan være PNG, JPG eller JPEG.
- Understøttelse af `.msg` skal undersøges i Fasit og må ikke antages.
- Returnér stabile id'er, status og afsendelsestidspunkt.
- Efterkontrollér create-kald, hvor Fasit giver mulighed for det.
- Ved mulig succesfuld oprettelse og fejlet efterkontrol skal fejlteksten advare imod dublet ved genkørsel.
- q-fasit må ikke importere SharePoint, ATS-states, q-word, q-gotenberg eller Signeringsportalen.
- Log aldrig CPR, filbytes, Base64, token eller credentials.
- Tilføj enhedstest med mocked `FasitApiClient` og manuelle integrationstests i repositoryets rod med navne som `test_local_*.py`.
