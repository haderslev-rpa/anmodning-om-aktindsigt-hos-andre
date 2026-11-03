"""Myndighedsbrev, samtykkebilag, q-word, Gotenberg og q-digitalpost."""
from __future__ import annotations

from datetime import date
from typing import Any

import configuration as cfg
from q_digitalpost.digital_post import DigitalPost
from q_digitalpost.models import DeliveryMethod, DigitalPostAttachment
from q_gotenberg.fil_til_pdf import konverter_fil_til_pdf
from q_sharepoint_api.functionality.sp_list_attachments import (
    add_attachment_to_item,
    get_attachments_for_item,
)
from q_sharepoint_api.sp_api import get_client
from q_word.udfyld_skabelon import udfyld_word_skabelon


def _required(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Oplysningerne mangler feltet {key!r}.")
    return value


def _dansk_dato(value: date) -> str:
    months = (
        "januar", "februar", "marts", "april", "maj", "juni",
        "juli", "august", "september", "oktober", "november", "december",
    )
    return f"{value.day}. {months[value.month - 1]} {value.year}"


def _hent_skabelon(file_name: str) -> bytes:
    client = get_client()
    site_id = client.get_site_id(cfg.SHAREPOINT_SITE_NAME)
    folder = str(cfg.SHAREPOINT_TEMPLATE_FOLDER).strip().strip("/")
    result = client.download_file_to_memory_by_path(
        site_id=site_id,
        file_path=f"{folder}/{str(file_name).strip().lstrip('/')}",
    )
    content = result.get("file_bytes") if isinstance(result, dict) else None
    if not isinstance(content, bytes) or not content.startswith(b"PK"):
        raise ValueError(f"Skabelonen {file_name!r} er ikke en gyldig DOCX.")
    return content


def _word_values(sharepoint_data: dict[str, Any]) -> dict[str, str]:
    return {
        "DATO": _dansk_dato(date.today()),
        "SAGSTYPE_OG_DATO": _required(sharepoint_data, cfg.FIELD_CASE_TYPE_AND_DATE),
        "INSTITUTIONSNAVN": _required(sharepoint_data, cfg.FIELD_INSTITUTION),
    }


def _opret_pdf(
    sharepoint_data: dict[str, Any],
    template_name: str,
    docx_name: str,
    placeholders: tuple[str, ...],
) -> bytes:
    values = _word_values(sharepoint_data)
    missing = [name for name in placeholders if name not in values]
    if missing:
        raise ValueError("Manglende Word-værdier: " + ", ".join(missing))
    docx = udfyld_word_skabelon(
        skabelon_bytes=_hent_skabelon(template_name),
        vaerdier=values,
    )
    pdf = konverter_fil_til_pdf(filnavn=docx_name, filindhold=docx)
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("Dokumentet blev ikke konverteret til en gyldig PDF.")
    return pdf


def opret_myndighedsbrev_pdf(sharepoint_data: dict[str, Any]) -> bytes:
    return _opret_pdf(
        sharepoint_data,
        cfg.MYNDIGHEDSBREV_TEMPLATE_FILE_NAME,
        cfg.MYNDIGHEDSBREV_DOCX_FILE_NAME,
        cfg.MYNDIGHEDSBREV_PLACEHOLDERS,
    )


def opret_rykkerbrev_pdf(sharepoint_data: dict[str, Any]) -> bytes:
    return _opret_pdf(
        sharepoint_data,
        cfg.RYKKERBREV_TEMPLATE_FILE_NAME,
        cfg.RYKKERBREV_DOCX_FILE_NAME,
        cfg.RYKKERBREV_PLACEHOLDERS,
    )


def gem_pdf_paa_sharepoint(item, pdf_bytes: bytes, file_name: str) -> None:
    sharepoint_data = item.data.get("box", {}).get("sharepoint")
    if not isinstance(sharepoint_data, dict):
        raise ValueError("ATS-itemet mangler box.sharepoint.")
    add_attachment_to_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=_required(sharepoint_data, cfg.FIELD_ID),
        file_name=file_name,
        file_bytes=pdf_bytes,
    )


def hent_samtykke_som_pdf(item) -> tuple[str, bytes]:
    sharepoint_data = item.data.get("box", {}).get("sharepoint")
    if not isinstance(sharepoint_data, dict):
        raise ValueError("ATS-itemet mangler box.sharepoint.")
    attachments = get_attachments_for_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=_required(sharepoint_data, cfg.FIELD_ID),
        include_file_bytes=True,
    )
    if not attachments:
        raise ValueError("SharePoint-itemet har ingen samtykkevedhæftning.")

    def name(entry: dict[str, Any]) -> str:
        return str(entry.get("file_name") or "").strip()

    signed = [
        entry for entry in attachments
        if name(entry).casefold().endswith(".pdf")
        and "samtykke" in name(entry).casefold()
        and "signeret" in name(entry).casefold()
        and "ikke signeret" not in name(entry).casefold()
    ]
    if len(signed) > 1:
        raise ValueError("Der blev fundet flere signerede samtykke-PDF'er.")
    if signed:
        pdf = signed[0].get("file_bytes")
        if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
            raise ValueError("Det signerede samtykke er ikke en gyldig PDF.")
        return cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME, pdf

    allowed = tuple(extension.casefold() for extension in cfg.CONSENT_IMAGE_EXTENSIONS)
    images = [
        entry for entry in attachments
        if name(entry).casefold().endswith(allowed)
        and "samtykke" in name(entry).casefold()
    ]
    if len(images) != 1:
        raise ValueError("Der skal findes præcis ét billede af samtykket, når signeret PDF mangler.")
    image = images[0]
    pdf = konverter_fil_til_pdf(
        filnavn=name(image),
        filindhold=image["file_bytes"],
    )
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("Samtykkebilledet blev ikke konverteret til en gyldig PDF.")
    return cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME, pdf


async def send_digitalpost(
    *,
    item,
    letter_pdf: bytes,
    letter_file_name: str,
    subject: str,
    consent_file_name: str,
    consent_pdf: bytes,
):
    sharepoint_data = item.data["box"]["sharepoint"]
    result = await DigitalPost().send_digital_post(
        pdf_content=letter_pdf,
        file_name=letter_file_name,
        cpr_or_cvr=_required(sharepoint_data, "CVR"),
        subject=subject,
        source_process="anmodning-om-aktindsigt-hos-andre",
        source_item_id=item.reference,
        delivery_method=DeliveryMethod.ONLY_DIGITAL_POST,
        attachments=[DigitalPostAttachment(content=consent_pdf, file_name=consent_file_name)],
        p_number=_required(sharepoint_data, cfg.FIELD_P_NUMBER),
        production_unit_name=_required(sharepoint_data, cfg.FIELD_INSTITUTION),
    )
    if result.status != "QUEUED":
        raise ValueError(
            "Digital Post blev ikke oprettet. "
            f"Status={result.status}, reason={result.reason}, message={result.message}"
        )
    return result
