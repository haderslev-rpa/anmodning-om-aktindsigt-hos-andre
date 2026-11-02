"""
Procestrin og forretningslogik for ét work item.

Denne første worker-del:

1. Genhenter det aktuelle SharePoint-item til box.sharepoint.
2. Kontrollerer om borgeren allerede har givet samtykke.
3. Henter Word-skabelonen fra SharePoint.
4. Udfylder skabelonen med q-word.
5. Konverterer DOCX til PDF med q-gotenberg.
6. Kontrollerer om CPR kan modtage Digital Post.
7. Opretter en opgave i Signeringsportalen.
8. Gemmer flow_id i box.signeringsportal.
9. Opdaterer SharePoint og sætter itemet til Pending user.

Filen bruger ikke andre lokale procesmoduler.
"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import logging
from typing import Any

import requests

from automation_server_client import WorkItemStatus

from q_gotenberg.fil_til_pdf import (
    konverter_fil_til_pdf,
)
from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_sharepoint_api.functionality.sp_list_item_ats_box import (
    get_to_ats_box,
    update_from_ats_box,
    )

from q_datafordeleren_api.functionality.datafordeler_use import (
    get_aktuel_navn_og_adresse,
)

from q_sharepoint_api.sp_api import get_client
from q_signeringsportalen_api import (
    SigneringsportalClient,
)
from q_word.udfyld_skabelon import (
    udfyld_word_skabelon,
)

import configuration as cfg


logger = logging.getLogger(__name__)


# ============================================================
# STATES
# ============================================================

class States:
    """
    Processtates for denne første del af processen.

    States gør processen genstartbar og forhindrer, at det samme
    signeringsflow oprettes flere gange.
    """

    BORGERBREV_GENERERET = (
        "0.5 Brev til borger genereret"
    )

    BORGERBREV_SENDT_TIL_SIGNERING = (
        "1.0 Brev sendt til borger og afventer signering"
    )


# ============================================================
# GENERELLE HJÆLPEFUNKTIONER
# ============================================================

def mangler_state(
    data: dict[str, Any],
    state: str,
) -> bool:
    """
    Returnerer True, hvis staten ikke allerede findes.
    """
    states = data.get("state", [])

    if not isinstance(states, list):
        return True

    return not any(
        isinstance(existing_state, str)
        and state in existing_state
        for existing_state in states
    )


def set_state(
    data: dict[str, Any],
    item,
    state: str,
) -> None:
    """
    Gemmer en gennemført state på ATS-itemet.
    """
    update_item_data(
        data,
        item=item,
        state=state,
    )


def required_text(
    source: dict[str, Any],
    field_name: str,
) -> str:
    """
    Henter et obligatorisk felt som tekst.

    Rejser ValueError, hvis feltet mangler eller er tomt.
    """
    value = source.get(field_name)

    if value is None:
        raise ValueError(
            "SharePoint-itemet mangler feltet "
            f"{field_name!r}."
        )

    text = str(value).strip()

    if not text:
        raise ValueError(
            "SharePoint-itemet har en tom værdi i feltet "
            f"{field_name!r}."
        )

    return text


def normalize_cpr(
    cpr: str,
) -> str:
    """
    Fjerner bindestreg og mellemrum fra CPR.

    Returnerer CPR som ti cifre.
    """
    normalized_cpr = "".join(
        character
        for character in str(cpr)
        if character.isdigit()
    )

    if len(normalized_cpr) != 10:
        raise ValueError(
            "CPR skal indeholde præcis 10 cifre."
        )

    return normalized_cpr


def dansk_dato(
    value: date,
) -> str:
    """
    Returnerer en dato som dansk tekst.

    Eksempel:
        2. november 2026
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



# ============================================================
# SHAREPOINT-LISTE
# ============================================================

def hent_aktuelle_sharepoint_data(
    item,
) -> dict[str, Any]:
    """
    Genhenter SharePoint-itemet og overskriver box.sharepoint.

    Returnerer:
        Det aktuelle SharePoint-item som dictionary.
    """
    data = item.data

    box = data.get("box")

    if not isinstance(box, dict):
        raise ValueError(
            "ATS-itemet mangler en gyldig box."
        )

    sharepoint_data = box.get("sharepoint")

    if not isinstance(sharepoint_data, dict):
        raise ValueError(
            "ATS-itemet mangler box.sharepoint fra produceren."
        )

    item_id = required_text(
        sharepoint_data,
        cfg.FIELD_ID,
    )

    current_sharepoint_data = get_to_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        list_item_id=item_id,
        item=item,
    )

    if not isinstance(
        current_sharepoint_data,
        dict,
    ):
        raise TypeError(
            "SharePoint-opslaget returnerede ikke en dictionary."
        )

    # get_to_ats_box() ændrer item.data lokalt.
    # update_item_data() gemmer den friske box på ATS-itemet.
    update_item_data(
        item.data,
        item=item,
    )

    return current_sharepoint_data


def gem_sharepoint_opdateringer(
    item,
    updates: dict[str, Any],
    *,
    robot_kommentar: str | None = None,
) -> dict[str, Any]:
    """
    Fletter ændringer ind i box.sharepoint og opdaterer SharePoint.

    update_from_ats_box() læser selv oplysningerne fra:

        item.data["box"]["sharepoint"]

    Returnerer:
        SharePoints friske version af liste-itemet.
    """
    data = item.data

    box = data.get("box")

    if not isinstance(box, dict):
        raise ValueError(
            "ATS-itemet mangler en gyldig box."
        )

    sharepoint_data = box.get("sharepoint")

    if not isinstance(sharepoint_data, dict):
        raise ValueError(
            "ATS-itemet mangler box.sharepoint."
        )

    if cfg.FIELD_ID not in sharepoint_data:
        raise ValueError(
            "SharePoint-dataene mangler id."
        )

    if not isinstance(updates, dict):
        raise TypeError(
            "updates skal være en dictionary."
        )

    # De ændrede felter skrives direkte i box.sharepoint.
    sharepoint_data.update(updates)

    saved_sharepoint_data = update_from_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item=item,
        robot_kommentar=robot_kommentar,
    )

    if not isinstance(
        saved_sharepoint_data,
        dict,
    ):
        raise TypeError(
            "SharePoint-opdateringen returnerede ikke "
            "en dictionary."
        )

    # update_from_ats_box() lægger SharePoints svar tilbage
    # i box.sharepoint. Derefter gemmes boxen i ATS.
    update_item_data(
        item.data,
        item=item,
    )

    return saved_sharepoint_data


def aflever_til_sagsbehandler(
    item,
    message: str,
) -> None:
    """
    Afleverer sagen til sagsbehandler ved en forretningsfejl.
    """
    gem_sharepoint_opdateringer(
        item=item,
        updates={
            cfg.FIELD_HANDLED_BY:
                cfg.VALUE_CASEWORKER,
        },
        robot_kommentar=message,
    )

    item.update_status(
        WorkItemStatus.PENDING_USER_ACTION.value,
        message,
    )

def hent_borgeroplysninger(
    cpr: str,
) -> dict[str, str]:
    """
    Henter borgerens aktuelle navn og adresse fra Datafordeleren.

    Input:
        CPR som ti cifre.

    Returnerer:
        {
            "navn": "Borgerens fulde navn",
            "adresse": "Vejnavn 1, 1. th.",
            "postnummer_by": "6100 Haderslev",
        }

    Rejser:
        ValueError, hvis CPR ikke findes, der mangler aktuelt navn
        eller adresse, eller Datafordeleren vurderer, at der ikke
        kan sendes brev.
    """
    normalized_cpr = normalize_cpr(cpr)

    result = get_aktuel_navn_og_adresse(
        normalized_cpr
    )

    if not isinstance(result, dict):
        raise TypeError(
            "Datafordeleren returnerede ikke en dictionary."
        )

    if not result.get("cpr_format_ok", False):
        raise ValueError(
            "Datafordeleren afviste CPR-formatet."
        )

    if not result.get("findes", False):
        raise ValueError(
            "CPR-nummeret blev ikke fundet i Datafordeleren."
        )

    if not result.get("har_aktuel_navn", False):
        raise ValueError(
            "Datafordeleren returnerede ikke et aktuelt navn."
        )

    if not result.get("har_aktuel_adresse", False):
        raise ValueError(
            "Datafordeleren returnerede ikke en aktuel adresse."
        )

    if not result.get("kan_sendes_brev", False):
        reason = str(
            result.get("kan_sendes_brev_aarsag") or ""
        ).strip()

        if not reason:
            reason = (
                "Datafordeleren vurderer, at der ikke kan sendes brev."
            )

        raise ValueError(reason)

    name = str(
        result.get("navn") or ""
    ).strip()

    address_line = str(
        result.get("adresse_linje") or ""
    ).strip()

    postal_code_and_city = str(
        result.get("by_postnr") or ""
    ).strip()

    if not name:
        raise ValueError(
            "Borgerens navn er tomt i Datafordeler-resultatet."
        )

    if not address_line:
        raise ValueError(
            "Borgerens adresselinje er tom "
            "i Datafordeler-resultatet."
        )

    if not postal_code_and_city:
        raise ValueError(
            "Borgerens postnummer og by er tomt "
            "i Datafordeler-resultatet."
        )

    return {
        "navn": name,
        "adresse": address_line,
        "postnummer_by": postal_code_and_city,
    }

# ============================================================
# WORD-SKABELON FRA SHAREPOINT
# ============================================================

def build_template_path(
    file_name: str,
) -> str:
    """
    Bygger en sti relativt til SharePoints standarddrev.

    Returnerer eksempelvis:

        RPA - Processer/Anmod om aktindsigt hos andre/
        Skabeloner/Samtykke_til_borger.docx
    """
    folder = (
        cfg.SHAREPOINT_TEMPLATE_FOLDER
        .strip()
        .strip("/")
    )

    validated_file_name = (
        str(file_name)
        .strip()
        .lstrip("/")
    )

    if not validated_file_name:
        raise ValueError(
            "Skabelonens filnavn må ikke være tomt."
        )

    return f"{folder}/{validated_file_name}"


def hent_skabelon_bytes(
    file_name: str,
) -> bytes:
    """
    Henter en Word-skabelon fra SharePoints standarddrev.

    Funktionen bruger q-sharepoint-api til:
        - SharePoint-klienten
        - site-id
        - drive-id
        - drive-item-id

    Returnerer:
        Filens indhold som bytes.
    """
    template_path = build_template_path(
        file_name
    )

    client = get_client()

    site_id = client.get_site_id(
        cfg.SHAREPOINT_SITE_NAME
    )

    drive_id = client.get_drive_id(
        site_id
    )

    drive_item_id = client.get_drive_item_id(
        site_id,
        template_path,
    )

    download_url = (
        f"{client.base}"
        f"/drives/{drive_id}"
        f"/items/{drive_item_id}"
        f"/content"
    )

    response = requests.get(
        download_url,
        headers=client.auth.graph_headers(),
        timeout=120,
        allow_redirects=True,
    )

    response.raise_for_status()

    template_bytes = response.content

    if not template_bytes:
        raise ValueError(
            "Word-skabelonen blev hentet, "
            "men filindholdet er tomt."
        )

    # DOCX er en ZIP-baseret fil og begynder normalt med PK.
    if not template_bytes.startswith(b"PK"):
        raise ValueError(
            "Den hentede skabelon ligner ikke en DOCX-fil."
        )

    return template_bytes


# ============================================================
# WORD OG GOTENBERG
# ============================================================

def build_borgerbrev_values(
    sharepoint_data: dict[str, Any],
) -> dict[str, object]:
    """
    Bygger samtlige værdier til borgerbrevets Word-skabelon.

    Navn og adresse hentes fra Datafordeleren på baggrund af CPR.
    Sagsoplysningerne kommer fra SharePoint.

    Returnerer:
        En dictionary, som kan sendes direkte til
        q-word.udfyld_word_skabelon().
    """
    cpr = normalize_cpr(
        required_text(
            sharepoint_data,
            cfg.FIELD_CPR,
        )
    )

    citizen = hent_borgeroplysninger(
        cpr
    )

    case_type_and_date = required_text(
        sharepoint_data,
        cfg.FIELD_CASE_TYPE_AND_DATE,
    )

    institution_name = required_text(
        sharepoint_data,
        cfg.FIELD_INSTITUTION,
    )

    return {
        "NAVN": citizen["navn"],
        "ADRESSE": citizen["adresse"],
        "POSTNUMMER_BY": citizen["postnummer_by"],
        "CPR": cpr,
        "DATO": dansk_dato(date.today()),
        "SAGSTYPE_OG_DATO": case_type_and_date,
        "INSTITUTIONSNAVN": institution_name,
    }


def opret_borgerbrev_pdf(
    sharepoint_data: dict[str, Any],
) -> bytes:
    """
    Henter skabelonen, udfylder Word og konverterer til PDF.

    Returnerer:
        Den færdige PDF som bytes.
    """
    template_bytes = hent_skabelon_bytes(
        cfg.BORGERBREV_TEMPLATE_FILE_NAME
    )

    values = build_borgerbrev_values(
        sharepoint_data
    )

    missing_values = [
        placeholder
        for placeholder in cfg.BORGERBREV_PLACEHOLDERS
        if placeholder not in values
    ]

    if missing_values:
        raise ValueError(
            "Der mangler værdier til Word-skabelonen: "
            + ", ".join(missing_values)
        )

    docx_bytes = udfyld_word_skabelon(
        skabelon_bytes=template_bytes,
        vaerdier=values,
    )

    if not isinstance(docx_bytes, bytes):
        raise TypeError(
            "q-word returnerede ikke DOCX som bytes."
        )

    pdf_bytes = konverter_fil_til_pdf(
        filnavn=cfg.BORGERBREV_DOCX_FILE_NAME,
        filindhold=docx_bytes,
    )

    if not isinstance(pdf_bytes, bytes):
        raise TypeError(
            "q-gotenberg returnerede ikke PDF som bytes."
        )

    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError(
            "q-gotenberg returnerede ikke en gyldig PDF."
        )

    return pdf_bytes


# ============================================================
# SIGNERINGSPORTALEN
# ============================================================

def opret_signeringsflow(
    *,
    cpr: str,
    pdf_bytes: bytes,
) -> dict[str, str]:
    """
    Kontrollerer Digital Post og opretter signeringsopgaven.

    Returnerer:
        En dictionary, der kan gemmes i box.signeringsportal.
    """
    normalized_cpr = normalize_cpr(cpr)
    created_at = datetime.now(timezone.utc)

    with (
        SigneringsportalClient
        .from_automation_server()
    ) as client:
        can_receive_digital_post = (
            client.kan_modtage_digital_post_cpr(
                normalized_cpr
            )
        )

        if not can_receive_digital_post:
            raise ValueError(
                "Borgeren kan ikke modtage Digital Post."
            )

        result = client.opret_signeringsopgave(
            navn=cfg.SIGNING_WORKFLOW_NAME,
            pdf_filer=[
                {
                    "titel":
                        cfg.BORGERBREV_PDF_FILE_NAME,
                    "indhold": pdf_bytes,
                }
            ],
            underskrivere=[
                {
                    "identifikator":
                        normalized_cpr,
                    "type":
                        cfg.SIGNING_RECIPIENT_TYPE,
                    "besked":
                        cfg.SIGNING_MESSAGE,
                }
            ],
            udloeber=(
                created_at
                + timedelta(
                    days=cfg.SIGNING_EXPIRY_DAYS
                )
            ),
            should_creator_sign=False,
            is_structured=False,
        )

    flow_id = str(
        result.flow_id or ""
    ).strip()

    if not flow_id:
        raise RuntimeError(
            "Signeringsportalen returnerede ikke flow_id."
        )

    return {
        "flow_id": flow_id,
        "status": "Pending",
        "created_at": created_at.isoformat(),
    }


def beregn_naeste_signeringskontrol() -> str:
    """
    Beregner næste tidspunkt for statuskontrol.

    Returnerer:
        ISO-formateret tidspunkt i UTC.
    """
    next_check = (
        datetime.now(timezone.utc)
        + timedelta(
            days=cfg.SIGNING_STATUS_CHECK_DAYS
        )
    )

    return next_check.isoformat()


def set_pending_user_action(
    item,
    message: str,
) -> None:
    """
    Sætter itemet til Pending user.
    """
    item.update_status(
        WorkItemStatus.PENDING_USER_ACTION.value,
        message,
    )


def send_borgerbrev_til_signering(
    item,
    sharepoint_data: dict[str, Any],
) -> None:
    """
    Genererer borgerbrevet og sender det til signering.

    Der oprettes ikke et nyt signeringsflow, hvis boxen allerede
    indeholder et flow_id.
    """
    data = item.data
    box = data["box"]

    signing_data = box.get(
        "signeringsportal"
    )

    if isinstance(signing_data, dict):
        existing_flow_id = str(
            signing_data.get("flow_id") or ""
        ).strip()

        if existing_flow_id:
            logger.info(
                "Item %s har allerede et signeringsflow.",
                item.reference,
            )

            set_pending_user_action(
                item,
                "Afventer digital signering.",
            )
            return

    if not mangler_state(
        data,
        States.BORGERBREV_SENDT_TIL_SIGNERING,
    ):
        logger.info(
            "State for afsendt borgerbrev findes allerede."
        )

        set_pending_user_action(
            item,
            "Afventer digital signering.",
        )
        return

    pdf_bytes = opret_borgerbrev_pdf(
        sharepoint_data
    )

    set_state(
        data,
        item,
        States.BORGERBREV_GENERERET,
    )

    new_signing_data = opret_signeringsflow(
        cpr=required_text(
            sharepoint_data,
            cfg.FIELD_CPR,
        ),
        pdf_bytes=pdf_bytes,
    )

    update_item_data(
        data,
        box_updates={
            "signeringsportal":
                new_signing_data,
        },
        item=item,
    )

    set_state(
        data,
        item,
        States.BORGERBREV_SENDT_TIL_SIGNERING,
    )

    gem_sharepoint_opdateringer(
        item=item,
        updates={},
        robot_kommentar=(
            "Brev er sendt til borger og afventer "
            "digital signering."
        ),
    )

    defer_until = (
        beregn_naeste_signeringskontrol()
    )

    update_item_data(
        data,
        item=item,
        defer=defer_until,
    )

    set_pending_user_action(
        item,
        (
            "Borgerbrev er sendt til signering. "
            f"Næste kontrol: {defer_until}"
        ),
    )

    logger.info(
        "Signeringsflow oprettet for item %s.",
        item.reference,
    )


# ============================================================
# HOVEDFUNKTION
# ============================================================

async def behandel_page(
    item,
    session,
    page,
) -> None:
    """
    Behandler ét ATS-item.

    main.py ejer browserens livscyklus og den overordnede
    fejlhåndtering.

    Denne procesdel bruger ikke Playwright endnu, men session
    og page bevares efter processkabelonens funktionssignatur.
    """
    del session
    del page

    data = item.data

    if not isinstance(data, dict):
        raise TypeError(
            "item.data skal være en dictionary."
        )

    box = data.get("box")

    if not isinstance(box, dict):
        raise ValueError(
            "Itemet mangler box fra queue_producer.py."
        )

    if not isinstance(
        box.get("sharepoint"),
        dict,
    ):
        raise ValueError(
            "Itemet mangler box.sharepoint "
            "fra queue_producer.py."
        )

    try:
        sharepoint_data = (
            hent_aktuelle_sharepoint_data(
                item
            )
        )

        consent = required_text(
            sharepoint_data,
            cfg.FIELD_CONSENT,
        )

        if consent != cfg.VALUE_NO:
            logger.info(
                "Borgerbrev skal ikke oprettes. "
                "Samtykkestatus er %r.",
                consent,
            )
            return

        send_borgerbrev_til_signering(
            item=item,
            sharepoint_data=sharepoint_data,
        )

    except ValueError as error:
        # Forretningsfejl afleveres til sagsbehandler.
        # Tekniske exceptions løftes videre til main.py.
        logger.warning(
            "Sagen kan ikke behandles automatisk: %s",
            error,
        )

        aflever_til_sagsbehandler(
            item=item,
            message=str(error),
        )