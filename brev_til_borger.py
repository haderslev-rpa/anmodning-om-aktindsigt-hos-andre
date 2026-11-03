"""Borgerbrev, Datafordeler, q-word, Gotenberg og Signeringsportalen."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import configuration as cfg
from q_datafordeleren_api.functionality.datafordeler_use import get_aktuel_navn_og_adresse
from q_gotenberg.fil_til_pdf import konverter_fil_til_pdf
from q_sharepoint_api.functionality.sp_list_attachments import add_attachment_to_item
from q_sharepoint_api.sp_api import get_client
from q_signeringsportalen_api import SigneringsportalClient
from q_word.udfyld_skabelon import udfyld_word_skabelon


def _required(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Oplysningerne mangler feltet {key!r}.")
    return value


def normalize_cpr(cpr: str) -> str:
    value = "".join(character for character in str(cpr) if character.isdigit())
    if len(value) != 10:
        raise ValueError("CPR skal indeholde præcis 10 cifre.")
    return value


def dansk_dato(value: date) -> str:
    months = (
        "januar", "februar", "marts", "april", "maj", "juni",
        "juli", "august", "september", "oktober", "november", "december",
    )
    return f"{value.day}. {months[value.month - 1]} {value.year}"


def hent_skabelon_bytes(file_name: str) -> bytes:
    folder = str(cfg.SHAREPOINT_TEMPLATE_FOLDER).strip().strip("/")
    name = str(file_name).strip().lstrip("/")
    if not folder or not name:
        raise ValueError("Skabelonmappe og filnavn skal være udfyldt.")
    client = get_client()
    site_id = client.get_site_id(cfg.SHAREPOINT_SITE_NAME)
    result = client.download_file_to_memory_by_path(
        site_id=site_id,
        file_path=f"{folder}/{name}",
    )
    content = result.get("file_bytes") if isinstance(result, dict) else None
    if not isinstance(content, bytes) or not content.startswith(b"PK"):
        raise ValueError(f"Skabelonen {name!r} er ikke en gyldig DOCX.")
    return content


def hent_borgeroplysninger(cpr: str) -> dict[str, str]:
    result = get_aktuel_navn_og_adresse(normalize_cpr(cpr))
    if not isinstance(result, dict):
        raise TypeError("Datafordeleren returnerede ikke en dictionary.")
    checks = (
        ("cpr_format_ok", "Datafordeleren afviste CPR-formatet."),
        ("findes", "CPR-nummeret blev ikke fundet i Datafordeleren."),
        ("har_aktuel_navn", "Der blev ikke fundet et aktuelt navn."),
        ("har_aktuel_adresse", "Der blev ikke fundet en aktuel adresse."),
        ("kan_sendes_brev", str(result.get("kan_sendes_brev_aarsag") or "Der kan ikke sendes brev.")),
    )
    for key, message in checks:
        if not result.get(key, False):
            raise ValueError(message)
    values = {
        "navn": str(result.get("navn") or "").strip(),
        "adresse": str(result.get("adresse_linje") or "").strip(),
        "postnummer_by": str(result.get("by_postnr") or "").strip(),
    }
    if not all(values.values()):
        raise ValueError("Datafordeleren returnerede ikke komplette adresseoplysninger.")
    return values


def opret_borgerbrev_pdf(sharepoint_data: dict[str, Any]) -> bytes:
    cpr = normalize_cpr(_required(sharepoint_data, cfg.FIELD_CPR))
    citizen = hent_borgeroplysninger(cpr)
    values = {
        "NAVN": citizen["navn"],
        "ADRESSE": citizen["adresse"],
        "POSTNUMMER_BY": citizen["postnummer_by"],
        "CPR": cpr,
        "DATO": dansk_dato(date.today()),
        "SAGSTYPE_OG_DATO": _required(sharepoint_data, cfg.FIELD_CASE_TYPE_AND_DATE),
        "INSTITUTIONSNAVN": _required(sharepoint_data, cfg.FIELD_INSTITUTION),
    }
    missing = [name for name in cfg.BORGERBREV_PLACEHOLDERS if name not in values]
    if missing:
        raise ValueError("Manglende Word-værdier: " + ", ".join(missing))
    docx = udfyld_word_skabelon(
        skabelon_bytes=hent_skabelon_bytes(cfg.BORGERBREV_TEMPLATE_FILE_NAME),
        vaerdier=values,
    )
    pdf = konverter_fil_til_pdf(
        filnavn=cfg.BORGERBREV_DOCX_FILE_NAME,
        filindhold=docx,
    )
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("Borgerbrevet blev ikke konverteret til en gyldig PDF.")
    return pdf


def gem_pdf_paa_sharepoint(item, pdf_bytes: bytes, file_name: str) -> None:
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Dokumentet er ikke en gyldig PDF.")
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


def opret_signeringsflow(cpr: str, pdf_bytes: bytes) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc)
    normalized_cpr = normalize_cpr(cpr)
    with SigneringsportalClient.from_automation_server() as client:
        if not client.kan_modtage_digital_post_cpr(normalized_cpr):
            raise ValueError("Borgeren kan ikke modtage Digital Post.")
        result = client.opret_signeringsopgave(
            navn=cfg.SIGNING_WORKFLOW_NAME,
            pdf_filer=[{"titel": cfg.BORGERBREV_PDF_FILE_NAME, "indhold": pdf_bytes}],
            underskrivere=[{
                "identifikator": normalized_cpr,
                "type": cfg.SIGNING_RECIPIENT_TYPE,
                "besked": cfg.SIGNING_MESSAGE,
            }],
            should_creator_sign=False,
            is_structured=False,
        )
    flow_id = str(result.flow_id or "").strip()
    if not flow_id:
        raise RuntimeError("Signeringsportalen returnerede ikke flow_id.")
    return {
        "flow_id": flow_id,
        "status": "Pending",
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
    }


def hent_signeringsstatus(flow_id: str):
    with SigneringsportalClient.from_automation_server() as client:
        return client.hent_signeringsstatus(flow_id)


def hent_signeret_pdf(flow_id: str) -> bytes:
    with SigneringsportalClient.from_automation_server() as client:
        pdf = client.hent_signeret_pdf(flow_id)
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("Signeringsportalen returnerede ikke en gyldig PDF.")
    return pdf
