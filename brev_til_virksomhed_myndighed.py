"""
Myndighedsbrev, rykkerbrev, Datafordeleren, samtykkebilag,
q-word, Gotenberg og q-digitalpost.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import configuration as cfg

from q_datafordeleren_api.functionality.datafordeler_use import (
    get_aktuel_navn_og_adresse,
)
from q_digitalpost.digital_post import DigitalPost
from q_digitalpost.models import (
    DeliveryMethod,
    DigitalPostAttachment,
)
from q_gotenberg.fil_til_pdf import (
    konverter_fil_til_pdf,
)
from q_sharepoint_api.functionality.sp_list_attachments import (
    add_attachment_to_item,
    get_attachments_for_item,
)
from q_sharepoint_api.sp_api import get_client
from q_word.udfyld_skabelon import (
    udfyld_word_skabelon,
)

class DigitalPostError(RuntimeError):
    """
    Teknisk fejl ved bestilling af Digital Post.

    Fejlen må ikke behandles som en forretningsfejl.
    Den skal gå videre til main.py, så work itemet fejler tydeligt
    og kan genstartes manuelt.
    """

# ============================================================
# GENERELLE HJÆLPEFUNKTIONER
# ============================================================



def _required(
    data: dict[str, Any],
    key: str,
) -> str:
    """
    Henter et obligatorisk felt som trimmet tekst.
    """
    value = str(
        data.get(key) or ""
    ).strip()

    if not value:
        raise ValueError(
            f"Oplysningerne mangler feltet {key!r}."
        )

    return value


def _normalize_cpr(
    cpr: str,
) -> str:
    """
    Normaliserer CPR til ti cifre.
    """
    value = "".join(
        character
        for character in str(cpr)
        if character.isdigit()
    )

    if len(value) != 10:
        raise ValueError(
            "CPR skal indeholde præcis 10 cifre."
        )

    return value


def _dansk_dato(
    value: date,
) -> str:
    """
    Returnerer eksempelvis:

        3. november 2026
    """
    months = (
        "januar",
        "februar",
        "marts",
        "april",
        "maj",
        "juni",
        "juli",
        "august",
        "september",
        "oktober",
        "november",
        "december",
    )

    return (
        f"{value.day}. "
        f"{months[value.month - 1]} "
        f"{value.year}"
    )


def _hent_borgers_navn(
    cpr: str,
) -> str:
    """
    Henter borgerens aktuelle navn fra Datafordeleren.

    Returnerer:
        Borgerens aktuelle navn som tekst.
    """
    normalized_cpr = _normalize_cpr(
        cpr
    )

    result = get_aktuel_navn_og_adresse(
        normalized_cpr
    )

    if not isinstance(result, dict):
        raise TypeError(
            "Datafordeleren returnerede ikke en dictionary."
        )

    if not result.get(
        "cpr_format_ok",
        False,
    ):
        raise ValueError(
            "Datafordeleren afviste CPR-formatet."
        )

    if not result.get(
        "findes",
        False,
    ):
        raise ValueError(
            "CPR-nummeret blev ikke fundet i Datafordeleren."
        )

    if not result.get(
        "har_aktuel_navn",
        False,
    ):
        raise ValueError(
            "Datafordeleren returnerede ikke et aktuelt navn."
        )

    name = str(
        result.get("navn") or ""
    ).strip()

    if not name:
        raise ValueError(
            "Datafordeleren returnerede et tomt navn."
        )

    return name


# ============================================================
# SAGSTYPE
# ============================================================

def _sagstype_uden_dato(
    case_type_and_date: str,
) -> str:
    """
    Fjerner den afsluttende bindestreg og dato fra højre side.

    Eksempler:

        "Flexjobsag - 01-01-2026"
            -> "Flexjobsag."

        "Anden-sagstype - 01-01-2026"
            -> "Anden-sagstype."

    Der opdeles fra højre, så bindestreger i selve sagstypen
    bevares.
    """
    text = str(
        case_type_and_date
    ).strip()

    if not text:
        raise ValueError(
            "Sagstype og dato må ikke være tom."
        )

    case_type, separator, _date_text = (
        text.rpartition(" - ")
    )

    if not separator:
        raise ValueError(
            "Feltet 'Sagstype og dato' skal have formatet "
            "'Sagstype - DD-MM-ÅÅÅÅ'."
        )

    case_type = case_type.strip()

    if not case_type:
        raise ValueError(
            "Sagstypen er tom efter fjernelse af datoen."
        )

    return f"{case_type.rstrip('.')}."


def _sagstype_til_match(
    case_type_and_date: str,
) -> str:
    """
    Returnerer sagstypen uden dato og uden afsluttende punktum.

    Værdien bruges kun til sammenligning med
    FLEXJOB_CASE_TYPE_VALUE.
    """
    case_type = _sagstype_uden_dato(
        case_type_and_date
    )

    return case_type.rstrip(".").strip()


def _fleksjobtekst(
    case_type_and_date: str,
) -> str:
    """
    Returnerer fleksjobteksten ved fleksjobsager.

    Ved alle andre sagstyper returneres tom tekst, så q-word
    fjerner [[FLEKSJOB]] fra dokumentet.
    """
    case_type = _sagstype_til_match(
        case_type_and_date
    )

    configured_case_type = str(
        cfg.FLEXJOB_CASE_TYPE_VALUE
    ).strip()

    if (
        case_type.casefold()
        == configured_case_type.casefold()
    ):
        return cfg.FLEXJOB_AUTHORITY_TEXT

    return ""


# ============================================================
# WORD-SKABELONER
# ============================================================

def _hent_skabelon(
    file_name: str,
) -> bytes:
    """
    Henter en DOCX-skabelon fra SharePoint til memory.
    """
    client = get_client()

    site_id = client.get_site_id(
        cfg.SHAREPOINT_SITE_NAME
    )

    folder = str(
        cfg.SHAREPOINT_TEMPLATE_FOLDER
    ).strip().strip("/")

    validated_file_name = str(
        file_name
    ).strip().lstrip("/")

    if not folder:
        raise ValueError(
            "SHAREPOINT_TEMPLATE_FOLDER må ikke være tom."
        )

    if not validated_file_name:
        raise ValueError(
            "Skabelonens filnavn må ikke være tomt."
        )

    result = (
        client.download_file_to_memory_by_path(
            site_id=site_id,
            file_path=(
                f"{folder}/{validated_file_name}"
            ),
        )
    )

    content = (
        result.get("file_bytes")
        if isinstance(result, dict)
        else None
    )

    if (
        not isinstance(content, bytes)
        or not content.startswith(b"PK")
    ):
        raise ValueError(
            f"Skabelonen {validated_file_name!r} "
            "er ikke en gyldig DOCX."
        )

    return content


def _myndighedsbrev_values(
    sharepoint_data: dict[str, Any],
) -> dict[str, str]:
    """
    Bygger samtlige Word-værdier til myndighedsbrevet.

    NAVN hentes fra Datafordeleren ved hjælp af CPR.

    CPR, institutionsnavn samt sagstype og dato hentes fra
    SharePoint-dataene i box.sharepoint.

    Returnerer:
        En dictionary, der kan sendes direkte til q-word.
    """
    cpr = _normalize_cpr(
        _required(
            sharepoint_data,
            cfg.FIELD_CPR,
        )
    )

    case_type_and_date = _required(
        sharepoint_data,
        cfg.FIELD_CASE_TYPE_AND_DATE,
    )

    return {
        "DATO": _dansk_dato(
            date.today()
        ),
        "NAVN": _hent_borgers_navn(
            cpr
        ),
        "CPR": cpr,
        "SAGSTYPE": _sagstype_uden_dato(
            case_type_and_date
        ),
        "INSTITUTIONSNAVN": _required(
            sharepoint_data,
            cfg.FIELD_INSTITUTION,
        ),
        "FLEKSJOB": _fleksjobtekst(
            case_type_and_date
        ),
    }


def _rykkerbrev_values(
    sharepoint_data: dict[str, Any],
) -> dict[str, str]:
    """
    Bygger samtlige Word-værdier til rykkerbrevet.
    """
    case_type_and_date = _required(
        sharepoint_data,
        cfg.FIELD_CASE_TYPE_AND_DATE,
    )

    return {
        "DATO": _dansk_dato(
            date.today()
        ),
        "SAGSTYPE": _sagstype_uden_dato(
            case_type_and_date
        ),
        "INSTITUTIONSNAVN": _required(
            sharepoint_data,
            cfg.FIELD_INSTITUTION,
        ),
    }


def _opret_pdf(
    *,
    values: dict[str, str],
    template_name: str,
    docx_name: str,
    placeholders: tuple[str, ...],
) -> bytes:
    """
    Udfylder en Word-skabelon og konverterer den til PDF.
    """
    missing = [
        name
        for name in placeholders
        if name not in values
    ]

    if missing:
        raise ValueError(
            "Manglende Word-værdier: "
            + ", ".join(missing)
        )

    docx = udfyld_word_skabelon(
        skabelon_bytes=_hent_skabelon(
            template_name
        ),
        vaerdier=values,
    )

    pdf = konverter_fil_til_pdf(
        filnavn=docx_name,
        filindhold=docx,
    )

    if (
        not isinstance(pdf, bytes)
        or not pdf.startswith(b"%PDF-")
    ):
        raise ValueError(
            "Dokumentet blev ikke konverteret "
            "til en gyldig PDF."
        )

    return pdf


def opret_myndighedsbrev_pdf(
    sharepoint_data: dict[str, Any],
) -> bytes:
    """
    Opretter den oprindelige myndighedsanmodning som PDF.
    """
    return _opret_pdf(
        values=_myndighedsbrev_values(
            sharepoint_data
        ),
        template_name=(
            cfg.MYNDIGHEDSBREV_TEMPLATE_FILE_NAME
        ),
        docx_name=(
            cfg.MYNDIGHEDSBREV_DOCX_FILE_NAME
        ),
        placeholders=(
            cfg.MYNDIGHEDSBREV_PLACEHOLDERS
        ),
    )


def opret_rykkerbrev_pdf(
    sharepoint_data: dict[str, Any],
) -> bytes:
    """
    Opretter rykkerbrevet som PDF.
    """
    return _opret_pdf(
        values=_rykkerbrev_values(
            sharepoint_data
        ),
        template_name=(
            cfg.RYKKERBREV_TEMPLATE_FILE_NAME
        ),
        docx_name=(
            cfg.RYKKERBREV_DOCX_FILE_NAME
        ),
        placeholders=(
            cfg.RYKKERBREV_PLACEHOLDERS
        ),
    )


# ============================================================
# SHAREPOINT-VEDHÆFTNINGER
# ============================================================

def _hent_sharepoint_data(
    item,
) -> dict[str, Any]:
    """
    Henter box.sharepoint fra ATS-itemet.
    """
    sharepoint_data = (
        item.data
        .get("box", {})
        .get("sharepoint")
    )

    if not isinstance(
        sharepoint_data,
        dict,
    ):
        raise ValueError(
            "ATS-itemet mangler box.sharepoint."
        )

    return sharepoint_data


def _hent_vedhaeftninger(
    item,
) -> list[dict[str, Any]]:
    """
    Henter alle SharePoint-listens vedhæftninger med filbytes.
    """
    sharepoint_data = _hent_sharepoint_data(
        item
    )

    attachments = get_attachments_for_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=_required(
            sharepoint_data,
            cfg.FIELD_ID,
        ),
        include_file_bytes=True,
    )

    if not isinstance(attachments, list):
        raise TypeError(
            "SharePoint returnerede ikke vedhæftninger "
            "som en liste."
        )

    return attachments


def _attachment_name(
    entry: dict[str, Any],
) -> str:
    """
    Henter filnavnet fra en SharePoint-vedhæftning.
    """
    return str(
        entry.get("file_name") or ""
    ).strip()


def _validated_pdf_from_attachment(
    entry: dict[str, Any],
    expected_name: str,
) -> bytes:
    """
    Henter og validerer PDF-bytes fra en vedhæftning.
    """
    pdf = entry.get("file_bytes")

    if (
        not isinstance(pdf, bytes)
        or not pdf.startswith(b"%PDF-")
    ):
        raise ValueError(
            f"SharePoint-filen {expected_name!r} "
            "er ikke en gyldig PDF."
        )

    return pdf

def hent_pdf_fra_sharepoint(
    item,
    file_name: str,
) -> bytes:
    """
    Henter præcis én navngiven PDF fra det aktuelle
    SharePoint-liste-item.

    Funktionen bruger filen, som faktisk ligger på SharePoint.
    Dokumentet regenereres derfor ikke i afsendelsestrinnet.

    Returnerer:
        PDF-filens indhold som bytes.

    Rejser:
        ValueError, hvis filen mangler, findes flere gange
        eller ikke indeholder en gyldig PDF.
    """
    sharepoint_data = (
        item.data
        .get("box", {})
        .get("sharepoint")
    )

    if not isinstance(
        sharepoint_data,
        dict,
    ):
        raise ValueError(
            "ATS-itemet mangler box.sharepoint."
        )

    item_id = _required(
        sharepoint_data,
        cfg.FIELD_ID,
    )

    expected_file_name = str(
        file_name
    ).strip()

    if not expected_file_name:
        raise ValueError(
            "Filnavnet må ikke være tomt."
        )

    attachments = get_attachments_for_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=item_id,
        include_file_bytes=True,
    )

    if not isinstance(
        attachments,
        list,
    ):
        raise TypeError(
            "SharePoint returnerede ikke vedhæftninger "
            "som en liste."
        )

    matches = [
        attachment
        for attachment in attachments
        if str(
            attachment.get("file_name") or ""
        ).strip().casefold()
        == expected_file_name.casefold()
    ]

    if not matches:
        raise ValueError(
            "Dokumentet blev ikke fundet på SharePoint: "
            f"{expected_file_name!r}."
        )

    if len(matches) > 1:
        raise ValueError(
            "Der blev fundet flere SharePoint-filer med "
            f"navnet {expected_file_name!r}."
        )

    pdf_bytes = matches[0].get(
        "file_bytes"
    )

    if not isinstance(
        pdf_bytes,
        bytes,
    ):
        raise TypeError(
            "SharePoint-vedhæftningen indeholder ikke "
            "file_bytes som bytes."
        )

    if not pdf_bytes.startswith(
        b"%PDF-"
    ):
        raise ValueError(
            f"SharePoint-filen {expected_file_name!r} "
            "er ikke en gyldig PDF."
        )

    return pdf_bytes

def gem_pdf_paa_sharepoint(
    item,
    pdf_bytes: bytes,
    file_name: str,
) -> None:
    """
    Gemmer en genereret PDF som vedhæftning på SharePoint-itemet.
    """
    if (
        not isinstance(pdf_bytes, bytes)
        or not pdf_bytes.startswith(b"%PDF-")
    ):
        raise ValueError(
            "Dokumentet er ikke en gyldig PDF."
        )

    sharepoint_data = _hent_sharepoint_data(
        item
    )

    add_attachment_to_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=_required(
            sharepoint_data,
            cfg.FIELD_ID,
        ),
        file_name=file_name,
        file_bytes=pdf_bytes,
    )


def hent_samtykke_som_pdf(
    item,
) -> tuple[str, bytes]:
    """
    Henter samtykkedokumentet som PDF.

    Digitalt samtykke:
        Bruger den signerede samtykke-PDF.

    Mundtligt/skriftligt samtykke:
        Finder ét samtykkebillede og konverterer det til PDF.
    """
    attachments = _hent_vedhaeftninger(
        item
    )

    signed = [
        entry
        for entry in attachments
        if (
            _attachment_name(entry).casefold()
            == cfg.SIGNED_CONSENT_FILE_NAME.casefold()
        )
    ]

    if len(signed) > 1:
        raise ValueError(
            "Der blev fundet flere signerede "
            "samtykke-PDF'er."
        )

    if signed:
        return (
            cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME,
            _validated_pdf_from_attachment(
                signed[0],
                cfg.SIGNED_CONSENT_FILE_NAME,
            ),
        )

    allowed_extensions = tuple(
        extension.casefold()
        for extension in cfg.CONSENT_IMAGE_EXTENSIONS
    )

    images = [
        entry
        for entry in attachments
        if (
            _attachment_name(entry)
            .casefold()
            .endswith(allowed_extensions)
            and "samtykke"
            in _attachment_name(entry).casefold()
        )
    ]

    if len(images) != 1:
        raise ValueError(
            "Der skal findes præcis ét billede af "
            "samtykket, når signeret PDF mangler."
        )

    image = images[0]

    image_name = _attachment_name(
        image
    )

    image_bytes = image.get(
        "file_bytes"
    )

    if not isinstance(image_bytes, bytes):
        raise ValueError(
            "Samtykkebilledet mangler filindhold."
        )

    pdf = konverter_fil_til_pdf(
        filnavn=image_name,
        filindhold=image_bytes,
    )

    if (
        not isinstance(pdf, bytes)
        or not pdf.startswith(b"%PDF-")
    ):
        raise ValueError(
            "Samtykkebilledet blev ikke konverteret "
            "til en gyldig PDF."
        )

    return (
        cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME,
        pdf,
    )


def hent_oprindeligt_myndighedsbrev(
    item,
) -> tuple[str, bytes]:
    """
    Henter præcis den myndighedsanmodning, som tidligere blev
    gemt på SharePoint.

    Brevet regenereres ikke i rykkerflowet.
    """
    attachments = _hent_vedhaeftninger(
        item
    )

    expected_name = (
        cfg.ARCHIVED_AUTHORITY_LETTER_FILE_NAME
    )

    matches = [
        entry
        for entry in attachments
        if (
            _attachment_name(entry).casefold()
            == expected_name.casefold()
        )
    ]

    if not matches:
        raise ValueError(
            "Det oprindelige myndighedsbrev mangler "
            f"på SharePoint: {expected_name!r}."
        )

    if len(matches) > 1:
        raise ValueError(
            "Der blev fundet flere vedhæftninger med "
            f"navnet {expected_name!r}."
        )

    return (
        expected_name,
        _validated_pdf_from_attachment(
            matches[0],
            expected_name,
        ),
    )


# ============================================================
# DIGITAL POST
# ============================================================

async def send_digitalpost(
    *,
    item,
    letter_pdf: bytes,
    letter_file_name: str,
    subject: str,
    attachments: list[tuple[str, bytes]],
):
    """
    Bestiller Digital Post med et hoveddokument og PDF-bilag.

    Returnerer:
        Resultatet fra q-digitalpost, når status er QUEUED.

    Rejser:
        ValueError ved ugyldige procesdata eller ugyldige dokumenter.

        DigitalPostError, hvis q-digitalpost ikke kan kontaktes,
        fejler teknisk eller ikke returnerer status QUEUED.

    Funktionen sætter ingen states og ændrer ikke SharePoint.
    Det gør behandel.py først efter en bekræftet bestilling.
    """
    if not isinstance(
        letter_pdf,
        bytes,
    ):
        raise ValueError(
            "Digital Posts hoveddokument skal være bytes."
        )

    if not letter_pdf.startswith(
        b"%PDF-"
    ):
        raise ValueError(
            "Digital Posts hoveddokument er ikke en gyldig PDF."
        )

    validated_letter_file_name = str(
        letter_file_name
    ).strip()

    if not validated_letter_file_name:
        raise ValueError(
            "Digital Posts hoveddokument mangler filnavn."
        )

    validated_subject = str(
        subject
    ).strip()

    if not validated_subject:
        raise ValueError(
            "Digital Post-forsendelsen mangler emne."
        )

    sharepoint_data = (
        item.data
        .get("box", {})
        .get("sharepoint")
    )

    if not isinstance(
        sharepoint_data,
        dict,
    ):
        raise ValueError(
            "ATS-itemet mangler box.sharepoint."
        )

    digitalpost_attachments = []

    used_file_names = {
        validated_letter_file_name.casefold()
    }

    for attachment_name, attachment_bytes in attachments:
        validated_attachment_name = str(
            attachment_name
        ).strip()

        if not validated_attachment_name:
            raise ValueError(
                "Et Digital Post-bilag mangler filnavn."
            )

        if (
            validated_attachment_name.casefold()
            in used_file_names
        ):
            raise ValueError(
                "Hoveddokument og bilag skal have "
                "forskellige filnavne. "
                f"Dublet: {validated_attachment_name!r}."
            )

        if not isinstance(
            attachment_bytes,
            bytes,
        ):
            raise ValueError(
                f"Bilaget {validated_attachment_name!r} "
                "skal være bytes."
            )

        if not attachment_bytes.startswith(
            b"%PDF-"
        ):
            raise ValueError(
                f"Bilaget {validated_attachment_name!r} "
                "er ikke en gyldig PDF."
            )

        used_file_names.add(
            validated_attachment_name.casefold()
        )

        digitalpost_attachments.append(
            DigitalPostAttachment(
                content=attachment_bytes,
                file_name=validated_attachment_name,
            )
        )

    try:
        result = await DigitalPost().send_digital_post(
            pdf_content=letter_pdf,
            file_name=validated_letter_file_name,
            cpr_or_cvr=_required(
                sharepoint_data,
                "CVR",
            ),
            subject=validated_subject,
            source_process=(
                "anmodning-om-aktindsigt-hos-andre"
            ),
            source_item_id=item.reference,
            delivery_method=(
                DeliveryMethod.ONLY_DIGITAL_POST
            ),
            attachments=digitalpost_attachments,
            p_number=_required(
                sharepoint_data,
                cfg.FIELD_P_NUMBER,
            ),
            production_unit_name=_required(
                sharepoint_data,
                cfg.FIELD_INSTITUTION,
            ),
        )

    except Exception as error:
        raise DigitalPostError(
            "Digital Post-bestillingen fejlede teknisk. "
            "Der er ikke sat sendt-state, og processen skal "
            "genstartes manuelt."
        ) from error

    if result is None:
        raise DigitalPostError(
            "q-digitalpost returnerede intet resultat. "
            "Der er ikke sat sendt-state."
        )

    result_status = str(
        getattr(result, "status", "")
        or ""
    ).strip()

    if result_status != "QUEUED":
        result_reason = str(
            getattr(result, "reason", "")
            or ""
        ).strip()

        result_message = str(
            getattr(result, "message", "")
            or ""
        ).strip()

        raise DigitalPostError(
            "q-digitalpost bekræftede ikke bestillingen. "
            f"Status={result_status!r}, "
            f"reason={result_reason!r}, "
            f"message={result_message!r}. "
            "Der er ikke sat sendt-state."
        )

    return result