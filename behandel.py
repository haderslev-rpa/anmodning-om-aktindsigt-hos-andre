"""
Procestrin og forretningslogik for ét work item.

Første kørsel:

1. Genhent det aktuelle SharePoint-item til box.sharepoint.
2. Kontrollér om borgeren allerede har givet samtykke.
3. Hent borgerens navn og adresse fra Datafordeleren.
4. Hent Word-skabelonen fra SharePoint.
5. Udfyld skabelonen med q-word.
6. Konvertér DOCX til PDF med q-gotenberg.
7. Kontrollér om CPR kan modtage Digital Post.
8. Opret et flow i Signeringsportalen.
9. Gem flow_id og created_at i box.signeringsportal.
10. Sæt state 1.0 og udsæt itemet til næste dag.

Efterfølgende kørsel:

1. Genhent det aktuelle SharePoint-item.
2. Se at state 1.0 allerede findes.
3. Spring dokumentgenerering og oprettelse af nyt flow over.
4. Kontrollér det eksisterende signeringsflow.
5. Ved Pending:
   - mindre end 14 dage: udsæt til næste dag
   - 14 dage eller mere: aflever til sagsbehandler
6. Ved Signed:
   - hent den signerede PDF
   - registrér digitalt samtykke
   - sæt state 1.5
7. Ved Inactive, Expired eller Deleted:
   - aflever til sagsbehandler

Filen bruger ikke andre lokale procesmoduler.
"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import logging
from typing import Any

from q_sharepoint_api.functionality.sp_list_attachments import (
    add_attachment_to_item,
    get_attachments_for_item,
)

from automation_server_client import WorkItemStatus

from q_datafordeleren_api.functionality.datafordeler_use import (
    get_aktuel_navn_og_adresse,
)
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
from q_sharepoint_api.sp_api import get_client
from q_signeringsportalen_api import (
    SigneringsportalClient,
)
from q_word.udfyld_skabelon import (
    udfyld_word_skabelon,
)

logger = logging.getLogger(__name__)


# ============================================================
# STATES
# ============================================================

class States:
    """
    Processtates for signering og myndighedsbrev.

    Kun procestrin, som er nødvendige for at undgå dobbelt
    afsendelse eller for at genoptage processen, får et state.
    """

    BORGERBREV_GENERERET = (
        "0.5 Brev til borger genereret"
    )

    BORGERBREV_SENDT_TIL_SIGNERING = (
        "1.0 Brev sendt til borger og afventer signering"
    )

    DIGITALT_SAMTYKKE_MODTAGET = (
        "1.5 Digitalt samtykke modtaget"
    )

    DIGITALT_SAMTYKKE_GEMT_SHAREPOINT = (
        "1.6 Signeret samtykke gemt i SharePoint"
    )

    MYNDIGHEDSBREV_SENDT = (
        "2.0 Sendt brev til virksomhed-myndighed"
    )

    MYNDIGHEDSBREV_GEMT_SHAREPOINT = (
        "2.2 Brev til virksomhed-myndighed gemt på SharePoint"
    )

    RYKKERBREV_SENDT = (
        "2.5 Rykkerbrev sendt til virksomhed-myndighed"
    )

    RYKKERBREV_GEMT_SHAREPOINT = (
        "2.7 Rykkerbrev gemt på SharePoint"
    )

def skal_behandles_af_robot(
    sharepoint_data: dict[str, Any],
) -> bool:
    """
    Returnerer True, når feltet "Behandles af" er "Robot".

    Sammenligningen er ikke følsom over for store og små bogstaver
    eller ekstra mellemrum.
    """
    handled_by = str(
        sharepoint_data.get(
            cfg.FIELD_HANDLED_BY
        )
        or ""
    ).strip()

    return (
        handled_by.casefold()
        == str(cfg.VALUE_ROBOT).strip().casefold()
    )

# ============================================================
# GENERELLE HJÆLPEFUNKTIONER
# ============================================================

def mangler_state(
    data: dict[str, Any],
    state: str,
) -> bool:
    """
    Returnerer True, hvis den angivne state ikke findes.

    ATS kan gemme ekstra tekst eller tidspunkt sammen med staten.
    Derfor kontrolleres det, om stateteksten indgår i værdien.
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
    Henter et obligatorisk felt som trimmet tekst.

    Rejser:
        ValueError, hvis feltet mangler eller er tomt.
    """
    value = source.get(field_name)

    if value is None:
        raise ValueError(
            "Oplysningerne mangler det obligatoriske felt "
            f"{field_name!r}."
        )

    text = str(value).strip()

    if not text:
        raise ValueError(
            "Oplysningerne har en tom værdi i feltet "
            f"{field_name!r}."
        )

    return text


def normalize_cpr(
    cpr: str,
) -> str:
    """
    Fjerner bindestreg og mellemrum fra CPR.

    Returnerer:
        CPR som præcis ti cifre.
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


def parse_utc_datetime(
    value: object,
    field_name: str,
) -> datetime:
    """
    Konverterer et ISO-tidspunkt til en timezone-aware UTC datetime.

    Understøtter blandt andet:

        2026-11-02T14:30:00+00:00
        2026-11-02T14:30:00Z

    Returnerer:
        datetime i UTC.
    """
    text = str(
        value or ""
    ).strip()

    if not text:
        raise ValueError(
            "box.signeringsportal mangler "
            f"{field_name!r}."
        )

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(
            text
        )
    except ValueError as error:
        raise ValueError(
            "Ugyldigt tidspunkt i "
            "box.signeringsportal"
            f"[{field_name!r}]: {value!r}."
        ) from error

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
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

    sharepoint_data = box.get(
        "sharepoint"
    )

    if not isinstance(
        sharepoint_data,
        dict,
    ):
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
    # Gem derefter de friske data på ATS-itemet.
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

    update_from_ats_box() læser direkte fra:

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

    sharepoint_data = box.get(
        "sharepoint"
    )

    if not isinstance(
        sharepoint_data,
        dict,
    ):
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

    sharepoint_data.update(
        updates
    )

    saved_sharepoint_data = (
        update_from_ats_box(
            site_name=cfg.SHAREPOINT_SITE_NAME,
            list_name=cfg.SHAREPOINT_LIST_NAME,
            item=item,
            robot_kommentar=robot_kommentar,
        )
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
    # i box.sharepoint. Gem derefter boxen på ATS-itemet.
    update_item_data(
        item.data,
        item=item,
    )

    return saved_sharepoint_data

def gem_pdf_som_sharepoint_vedhaeftning(
    *,
    item,
    pdf_bytes: bytes,
    file_name: str,
) -> None:
    """
    Gemmer PDF-bytes direkte fra memory som vedhæftning
    på SharePoint-liste-itemet.

    Der oprettes ingen midlertidig fil.
    """
    if not isinstance(pdf_bytes, bytes):
        raise TypeError(
            "pdf_bytes skal være bytes."
        )

    if not pdf_bytes:
        raise ValueError(
            "pdf_bytes må ikke være tom."
        )

    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError(
            "Dokumentet er ikke en gyldig PDF."
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

    item_id = required_text(
        sharepoint_data,
        cfg.FIELD_ID,
    )

    add_attachment_to_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=item_id,
        file_name=file_name,
        file_bytes=pdf_bytes,
    )

    logger.info(
        "PDF er gemt direkte fra memory som "
        "SharePoint-vedhæftning: %s",
        file_name,
    )

def aflever_til_sagsbehandler(
    item,
    message: str,
) -> None:
    """
    Afleverer sagen til sagsbehandler.

    Funktionen:
        1. Sætter "Behandles af" til "Sagsbehandler".
        2. Opdaterer SharePoint med en kort Robotkommentar.
        3. Genhenter SharePoint-itemet.
        4. Gemmer de friske SharePoint-data på ATS-itemet.
        5. Sætter ATS-itemet til Pending user.
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

    item_id = required_text(
        sharepoint_data,
        cfg.FIELD_ID,
    )

    validated_message = str(
        message or ""
    ).strip()

    if not validated_message:
        raise ValueError(
            "Robotkommentaren må ikke være tom."
        )

    # Ændr kun den egentlige SharePoint-oplysning i boxen.
    sharepoint_data[
        cfg.FIELD_HANDLED_BY
    ] = cfg.VALUE_CASEWORKER

    # Gem ændringen i SharePoint.
    # Robotkommentaren sendes særskilt og lægges ikke manuelt i boxen.
    update_from_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item=item,
        robot_kommentar=validated_message,
    )

    # Genhent straks SharePoint-itemet, så box.sharepoint indeholder
    # den aktuelle version, inklusive den nye Robotkommentar.
    get_to_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        list_item_id=item_id,
        item=item,
    )

    # Gem de genhentede SharePoint-oplysninger på ATS-itemet.
    update_item_data(
        item.data,
        item=item,
    )

    # ATS-statusbeskeden må gerne være den samme forklaring.
    item.update_status(
        WorkItemStatus.PENDING_USER_ACTION.value,
        validated_message,
    )


# ============================================================
# DATAFORDELEREN
# ============================================================

def hent_borgeroplysninger(
    cpr: str,
) -> dict[str, str]:
    """
    Henter borgerens aktuelle navn og adresse fra Datafordeleren.

    Returnerer:

        {
            "navn": "Borgerens fulde navn",
            "adresse": "Vejnavn 1, 1. th.",
            "postnummer_by": "6100 Haderslev",
        }

    Rejser:
        ValueError, hvis der mangler brugbare borgeroplysninger.
    """
    normalized_cpr = normalize_cpr(
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

    if not result.get(
        "har_aktuel_adresse",
        False,
    ):
        raise ValueError(
            "Datafordeleren returnerede ikke en aktuel adresse."
        )

    if not result.get(
        "kan_sendes_brev",
        False,
    ):
        reason = str(
            result.get(
                "kan_sendes_brev_aarsag"
            )
            or ""
        ).strip()

        if not reason:
            reason = (
                "Datafordeleren vurderer, at der ikke "
                "kan sendes brev."
            )

        raise ValueError(
            reason
        )

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
        "postnummer_by":
            postal_code_and_city,
    }


# ============================================================
# WORD-SKABELON FRA SHAREPOINT
# ============================================================

def build_template_path(
    file_name: str,
) -> str:
    """
    Bygger skabelonens sti relativt til SharePoints standarddrev.
    """
    folder = str(
        cfg.SHAREPOINT_TEMPLATE_FOLDER
    ).strip().strip("/")

    validated_file_name = (
        str(file_name)
        .strip()
        .lstrip("/")
    )

    if not folder:
        raise ValueError(
            "SHAREPOINT_TEMPLATE_FOLDER må ikke være tom."
        )

    if not validated_file_name:
        raise ValueError(
            "Skabelonens filnavn må ikke være tomt."
        )

    return (
        f"{folder}/"
        f"{validated_file_name}"
    )


def hent_skabelon_bytes(
    file_name: str,
) -> bytes:
    """
    Henter Word-skabelonen med q-sharepoint-api-funktionen:

        download_file_to_memory_by_path()

    Returnerer:
        DOCX-indhold som bytes.
    """
    template_path = build_template_path(
        file_name
    )

    logger.info(
        "Henter Word-skabelon fra SharePoint: %s",
        template_path,
    )

    client = get_client()

    site_id = client.get_site_id(
        cfg.SHAREPOINT_SITE_NAME
    )

    result = (
        client.download_file_to_memory_by_path(
            site_id=site_id,
            file_path=template_path,
        )
    )

    if not isinstance(result, dict):
        raise TypeError(
            "download_file_to_memory_by_path() "
            "returnerede ikke en dictionary."
        )

    template_bytes = result.get(
        "file_bytes"
    )

    returned_file_name = str(
        result.get("filename") or ""
    ).strip()

    if not isinstance(
        template_bytes,
        bytes,
    ):
        raise TypeError(
            "SharePoint-downloadet indeholder ikke "
            "'file_bytes' som bytes."
        )

    if not template_bytes:
        raise ValueError(
            "Word-skabelonen blev hentet fra SharePoint, "
            "men filindholdet var tomt."
        )

    if not template_bytes.startswith(
        b"PK"
    ):
        raise ValueError(
            "Den hentede SharePoint-fil er ikke en gyldig "
            f"DOCX-fil. Filnavn: {returned_file_name!r}."
        )

    logger.info(
        "Word-skabelon hentet fra SharePoint: %s",
        returned_file_name or file_name,
    )

    return template_bytes


# ============================================================
# WORD OG GOTENBERG
# ============================================================

def build_borgerbrev_values(
    sharepoint_data: dict[str, Any],
) -> dict[str, object]:
    """
    Bygger alle værdier til borgerbrevets Word-skabelon.

    Navn og adresse kommer fra Datafordeleren.
    Sagsoplysningerne kommer fra SharePoint.
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
        "NAVN":
            citizen["navn"],
        "ADRESSE":
            citizen["adresse"],
        "POSTNUMMER_BY":
            citizen["postnummer_by"],
        "CPR":
            cpr,
        "DATO":
            dansk_dato(date.today()),
        "SAGSTYPE_OG_DATO":
            case_type_and_date,
        "INSTITUTIONSNAVN":
            institution_name,
    }


def opret_borgerbrev_pdf(
    sharepoint_data: dict[str, Any],
) -> bytes:
    """
    Henter Word-skabelonen, udfylder den og konverterer til PDF.

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
        for placeholder
        in cfg.BORGERBREV_PLACEHOLDERS
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

    if not isinstance(
        docx_bytes,
        bytes,
    ):
        raise TypeError(
            "q-word returnerede ikke DOCX som bytes."
        )

    pdf_bytes = konverter_fil_til_pdf(
        filnavn=cfg.BORGERBREV_DOCX_FILE_NAME,
        filindhold=docx_bytes,
    )

    if not isinstance(
        pdf_bytes,
        bytes,
    ):
        raise TypeError(
            "q-gotenberg returnerede ikke PDF som bytes."
        )

    if not pdf_bytes.startswith(
        b"%PDF-"
    ):
        raise ValueError(
            "q-gotenberg returnerede ikke en gyldig PDF."
        )

    return pdf_bytes


# ============================================================
# SIGNERINGSPORTALEN
# ============================================================

def get_signeringsportal_data(
    item,
) -> dict[str, Any]:
    """
    Henter og validerer box.signeringsportal.

    Returnerer:
        Dictionary med flow_id, status og tidspunkter.
    """
    box = item.data.get("box")

    if not isinstance(box, dict):
        raise ValueError(
            "ATS-itemet mangler en gyldig box."
        )

    signing_data = box.get(
        "signeringsportal"
    )

    if not isinstance(
        signing_data,
        dict,
    ):
        raise ValueError(
            "ATS-itemet mangler box.signeringsportal."
        )

    return signing_data


def opret_signeringsflow(
    *,
    cpr: str,
    pdf_bytes: bytes,
) -> dict[str, Any]:
    """
    Kontrollerer Digital Post og opretter signeringsflowet.

    SIGNING_EXPIRY_DAYS sendes ikke til Signeringsportalen.
    Processens 14-dagesfrist håndteres lokalt ud fra created_at.

    Returnerer:
        Dictionary til box.signeringsportal.
    """
    normalized_cpr = normalize_cpr(
        cpr
    )

    created_at = datetime.now(
        timezone.utc
    )

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
                    "indhold":
                        pdf_bytes,
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
        "flow_id":
            flow_id,
        "status":
            "Pending",
        "created_at":
            created_at.isoformat(),
        "updated_at":
            created_at.isoformat(),
    }


def er_signeringsfrist_udloebet(
    signing_data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """
    Kontrollerer den lokale signeringsfrist.

    Fristen er:

        created_at + cfg.SIGNING_EXPIRY_DAYS

    Returnerer:
        True, hvis fristen er nået eller overskredet.
        False, hvis processen stadig er inden for fristen.
    """
    current_time = (
        now
        if now is not None
        else datetime.now(timezone.utc)
    )

    if current_time.tzinfo is None:
        current_time = current_time.replace(
            tzinfo=timezone.utc
        )

    current_time = current_time.astimezone(
        timezone.utc
    )

    created_at = parse_utc_datetime(
        signing_data.get("created_at"),
        "created_at",
    )

    deadline = (
        created_at
        + timedelta(
            days=cfg.SIGNING_EXPIRY_DAYS
        )
    )

    return current_time >= deadline


def udsaet_til_naeste_dag(
    item,
    message: str,
) -> None:
    """
    Udsætter sagen præcis én dag.

    SIGNING_EXPIRY_DAYS bruges ikke som defer-periode.
    """
    defer_until = (
        datetime.now(timezone.utc)
        + timedelta(days=1)
    )

    defer_text = (
        defer_until.isoformat()
    )

    update_item_data(
        item.data,
        item=item,
        defer=defer_text,
    )

    item.update_status(
        WorkItemStatus.PENDING_USER_ACTION.value,
        (
            f"{message} "
            f"Næste kontrol: {defer_text}"
        ),
    )


def behandl_modtaget_signering(
    *,
    item,
    client: SigneringsportalClient,
    status,
    signing_data: dict[str, Any],
) -> bytes:
    """
    Henter den signerede PDF og gemmer den på SharePoint-itemet.

    Efter en gennemført upload:
        - sættes state 1.5
        - sættes state 1.6
        - "Er der givet samtykke?" sættes til "Ja"
        - der skrives en Robotkommentar

    Returnerer:
        Den signerede PDF som bytes.
    """
    signed_pdf_bytes = (
        client.hent_signeret_pdf(
            signing_data["flow_id"]
        )
    )

    if not isinstance(
        signed_pdf_bytes,
        bytes,
    ):
        raise TypeError(
            "Signeringsportalen returnerede ikke "
            "den signerede PDF som bytes."
        )

    if not signed_pdf_bytes.startswith(
        b"%PDF-"
    ):
        raise ValueError(
            "Signeringsportalen returnerede ikke "
            "en gyldig PDF."
        )

    now = datetime.now(
        timezone.utc
    )

    signing_data["status"] = (
        status.status
    )
    signing_data["updated_at"] = (
        now.isoformat()
    )
    signing_data["signed_at"] = (
        now.isoformat()
    )
    signing_data[
        "signed_pdf_received"
    ] = True

    update_item_data(
        item.data,
        item=item,
    )

    if mangler_state(
        item.data,
        States.DIGITALT_SAMTYKKE_MODTAGET,
    ):
        set_state(
            item.data,
            item,
            States.DIGITALT_SAMTYKKE_MODTAGET,
        )

    if mangler_state(
        item.data,
        States.DIGITALT_SAMTYKKE_GEMT_SHAREPOINT,
    ):
        gem_pdf_som_sharepoint_vedhaeftning(
            item=item,
            pdf_bytes=signed_pdf_bytes,
            file_name=(
                cfg.SIGNED_CONSENT_FILE_NAME
            ),
        )

        set_state(
            item.data,
            item,
            States.DIGITALT_SAMTYKKE_GEMT_SHAREPOINT,
        )

    gem_sharepoint_opdateringer(
        item=item,
        updates={
            cfg.FIELD_CONSENT:
                cfg.VALUE_YES,
        },
        robot_kommentar=(
            "Borgerens digitale signering er modtaget. "
            "Det signerede samtykke er gemt som vedhæftning."
        ),
    )

    logger.info(
        "Digital signering er modtaget og gemt "
        "for item %s.",
        item.reference,
    )

    return signed_pdf_bytes


def kontroller_eksisterende_signeringsflow(
    item,
) -> bytes | None:
    """
    Kontrollerer et allerede oprettet signeringsflow.

    Signed:
        Hent den signerede PDF og registrér digitalt samtykke.

    Pending og under 14 dage:
        Udsæt sagen én dag.

    Pending og 14 dage eller mere:
        Aflever sagen til sagsbehandler via SharePoint.

    Inactive, Expired eller Deleted:
        Aflever sagen til sagsbehandler.

    Returnerer:
        Signeret PDF som bytes ved Signed.
        None ved Pending eller afsluttet uden signatur.
    """
    signing_data = (
        get_signeringsportal_data(
            item
        )
    )

    flow_id = required_text(
        signing_data,
        "flow_id",
    )

    with (
        SigneringsportalClient
        .from_automation_server()
    ) as client:
        status = (
            client.hent_signeringsstatus(
                flow_id
            )
        )

        now = datetime.now(
            timezone.utc
        )

        signing_data["status"] = (
            status.status
        )

        signing_data["updated_at"] = (
            now.isoformat()
        )

        update_item_data(
            item.data,
            item=item,
        )

        # Kontrollér Signed før den lokale frist.
        # En signering på dag 14 skal stadig behandles.
        if status.er_signeret:
            return behandl_modtaget_signering(
                item=item,
                client=client,
                status=status,
                signing_data=signing_data,
            )

        if status.afventer:
            if er_signeringsfrist_udloebet(
                signing_data,
                now=now,
            ):
                aflever_til_sagsbehandler(
                    item=item,
                    message=(
                        "Borgeren har ikke underskrevet "
                        "samtykket inden for "
                        f"{cfg.SIGNING_EXPIRY_DAYS} dage. "
                        "Sagen skal behandles manuelt."
                    ),
                )

                return None

            logger.info(
                "Signeringsflowet afventer fortsat "
                "borgeren for item %s. "
                "Sagen udsættes til næste dag.",
                item.reference,
            )

            udsaet_til_naeste_dag(
                item=item,
                message=(
                    "Afventer borgerens digitale signering."
                ),
            )

            return None

        if status.er_afsluttet_uden_signatur:
            aflever_til_sagsbehandler(
                item=item,
                message=(
                    "Signeringsforløbet blev afsluttet "
                    "uden underskrift. "
                    f"Status: {status.status}."
                ),
            )

            return None

        raise RuntimeError(
            "Signeringsportalen returnerede en "
            "status, som processen ikke håndterer: "
            f"{status.status!r}."
        )


def send_borgerbrev_til_signering(
    item,
    sharepoint_data: dict[str, Any],
) -> None:
    """
    Genererer borgerbrevet, sender det til signering og gemmer
    den ikke-signerede PDF som SharePoint-vedhæftning.

    State 1.0 sættes først, når:
        - signeringsflowet er oprettet
        - flow_id er gemt i box
        - den ikke-signerede PDF er gemt på SharePoint
        - Robotkommentaren er skrevet

    Hvis flow_id allerede findes, oprettes flowet ikke igen.
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
                "Item %s har allerede et signeringsflow. "
                "Nyt flow oprettes ikke.",
                item.reference,
            )

            # State 1.0 mangler, men flowet findes.
            # Det betyder typisk, at uploaden eller kommentaren
            # fejlede ved forrige kørsel.
            if mangler_state(
                data,
                States.BORGERBREV_SENDT_TIL_SIGNERING,
            ):
                regenerated_pdf = (
                    opret_borgerbrev_pdf(
                        sharepoint_data
                    )
                )

                gem_pdf_som_sharepoint_vedhaeftning(
                    item=item,
                    pdf_bytes=regenerated_pdf,
                    file_name=(
                        cfg.UNSIGNED_CONSENT_FILE_NAME
                    ),
                )

                gem_sharepoint_opdateringer(
                    item=item,
                    updates={},
                    robot_kommentar=(
                        "Den ikke-signerede PDF er "
                        "regenereret og gemt i dag."
                    ),
                )

                set_state(
                    data,
                    item,
                    States.BORGERBREV_SENDT_TIL_SIGNERING,
                )

                udsaet_til_naeste_dag(
                    item=item,
                    message=(
                        "Afventer digital signering."
                    ),
                )

                return

            kontroller_eksisterende_signeringsflow(
                item
            )

            return

    if not mangler_state(
        data,
        States.BORGERBREV_SENDT_TIL_SIGNERING,
    ):
        logger.info(
            "State %r findes allerede. "
            "Dokumentgenerering springes over.",
            States.BORGERBREV_SENDT_TIL_SIGNERING,
        )

        kontroller_eksisterende_signeringsflow(
            item
        )

        return

    pdf_bytes = opret_borgerbrev_pdf(
        sharepoint_data
    )

    if mangler_state(
        data,
        States.BORGERBREV_GENERERET,
    ):
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

    # Gem den præcise PDF, som blev sendt til Signeringsportalen.
    # Der bruges ikke en ekstra state til denne upload.
    gem_pdf_som_sharepoint_vedhaeftning(
        item=item,
        pdf_bytes=pdf_bytes,
        file_name=(
            cfg.UNSIGNED_CONSENT_FILE_NAME
        ),
    )

    gem_sharepoint_opdateringer(
        item=item,
        updates={},
        robot_kommentar=(
            "Brev er sendt til signering. "
            "Den vedhæftede PDF er ikke signeret."
        ),
    )

    # State 1.0 sættes til sidst, når hele trinnet er gennemført.
    set_state(
        data,
        item,
        States.BORGERBREV_SENDT_TIL_SIGNERING,
    )

    udsaet_til_naeste_dag(
        item=item,
        message=(
            "Borgerbrev er sendt til signering."
        ),
    )

    logger.info(
        "Signeringsflow oprettet for item %s.",
        item.reference,
    )

def hent_samtykke_som_pdf(
    item,
) -> tuple[str, bytes]:
    """
    Finder et samtykkedokument blandt SharePoint-itemets
    attachments og returnerer det som PDF i memory.

    Prioritet:

        1. Signeret samtykke som PDF.
        2. En anden PDF med "samtykke" i filnavnet, men ikke
           en fil markeret som "ikke signeret".
        3. Et billede med "samtykke" i filnavnet.

    Billeder konverteres til PDF gennem q-gotenberg. q-gotenberg
    bruger selv Pillow internt.

    Returnerer:
        Tuple med:
            - filnavnet, som Digital Post skal vise
            - PDF-filens indhold som bytes
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

    item_id = required_text(
        sharepoint_data,
        cfg.FIELD_ID,
    )

    attachments = get_attachments_for_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=item_id,
        include_file_bytes=True,
    )

    if not attachments:
        raise ValueError(
            "SharePoint-itemet har ingen vedhæftninger."
        )

    signed_pdf = next(
        (
            attachment
            for attachment in attachments
            if (
                attachment["file_name"]
                .casefold()
                .endswith(".pdf")
                and "samtykke" in (
                    attachment["file_name"]
                    .casefold()
                )
                and "signeret" in (
                    attachment["file_name"]
                    .casefold()
                )
                and "ikke signeret" not in (
                    attachment["file_name"]
                    .casefold()
                )
            )
        ),
        None,
    )

    if signed_pdf is not None:
        pdf_bytes = signed_pdf[
            "file_bytes"
        ]

        if not pdf_bytes.startswith(
            b"%PDF-"
        ):
            raise ValueError(
                "Det signerede samtykke har filendelsen "
                ".pdf, men indholdet er ikke en gyldig PDF."
            )

        return (
            cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME,
            pdf_bytes,
        )

    other_consent_pdf = next(
        (
            attachment
            for attachment in attachments
            if (
                attachment["file_name"]
                .casefold()
                .endswith(".pdf")
                and "samtykke" in (
                    attachment["file_name"]
                    .casefold()
                )
                and "ikke signeret" not in (
                    attachment["file_name"]
                    .casefold()
                )
            )
        ),
        None,
    )

    if other_consent_pdf is not None:
        pdf_bytes = other_consent_pdf[
            "file_bytes"
        ]

        if not pdf_bytes.startswith(
            b"%PDF-"
        ):
            raise ValueError(
                "Samtykkedokumentet har filendelsen "
                ".pdf, men indholdet er ikke en gyldig PDF."
            )

        return (
            cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME,
            pdf_bytes,
        )

    allowed_image_extensions = tuple(
        extension.casefold()
        for extension
        in cfg.CONSENT_IMAGE_EXTENSIONS
    )

    consent_images = [
        attachment
        for attachment in attachments
        if (
            "samtykke" in (
                attachment["file_name"]
                .casefold()
            )
            and (
                attachment["file_name"]
                .casefold()
                .endswith(
                    allowed_image_extensions
                )
            )
        )
    ]

    if not consent_images:
        raise ValueError(
            "Der blev ikke fundet en signeret PDF eller "
            "et billede af samtykket på SharePoint-itemet."
        )

    if len(consent_images) > 1:
        file_names = [
            attachment["file_name"]
            for attachment in consent_images
        ]

        raise ValueError(
            "Der blev fundet flere billeder af samtykket. "
            "Processen kan ikke vælge sikkert mellem: "
            + ", ".join(file_names)
        )

    selected_image = consent_images[0]

    image_pdf_bytes = (
        konverter_fil_til_pdf(
            filnavn=selected_image[
                "file_name"
            ],
            filindhold=selected_image[
                "file_bytes"
            ],
        )
    )

    if not image_pdf_bytes.startswith(
        b"%PDF-"
    ):
        raise ValueError(
            "q-gotenberg returnerede ikke en gyldig PDF "
            "fra samtykkebilledet."
        )

    return (
        cfg.AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME,
        image_pdf_bytes,
    )

async def behandl_myndighedsbrev(
    item,
    sharepoint_data: dict[str, Any],
) -> None:
    """
    Sender eller arkiverer myndighedsbrevet.

    Flow:

        State 2.0 mangler:
            Generér PDF.
            Send Digital Post.
            Sæt state 2.0.
            Gem PDF på SharePoint.
            Sæt state 2.2.

        State 2.0 findes, men 2.2 mangler:
            Generér PDF igen.
            Send ikke brevet igen.
            Gem den regenererede PDF på SharePoint.
            Skriv en kort Robotkommentar.
            Sæt state 2.2.

        Både 2.0 og 2.2 findes:
            Gå videre til fristkontrollen.
    """
    if mangler_state(
        item.data,
        States.MYNDIGHEDSBREV_SENDT,
    ):
        pdf_bytes = (
            opret_myndighedsbrev_pdf(
                sharepoint_data
            )
        )

        consent_file_name, consent_pdf_bytes = (
            hent_samtykke_som_pdf(
                item
            )
        )

        result = await send_myndighedsbrev_digitalpost(
            item=item,
            letter_pdf_bytes=pdf_bytes,
            consent_file_name=consent_file_name,
            consent_pdf_bytes=consent_pdf_bytes,
        )

        sent_at = datetime.now(
            timezone.utc
        )

        update_item_data(
            item.data,
            box_updates={
                "myndighedsbrev": {
                    "forsendelses_id":
                        result.forsendelses_id,
                    "sent_at":
                        sent_at.isoformat(),
                }
            },
            item=item,
        )

        set_state(
            item.data,
            item,
            States.MYNDIGHEDSBREV_SENDT,
        )

        gem_sharepoint_opdateringer(
            item=item,
            updates={
                cfg.FIELD_LETTER_STATUS:
                    cfg.VALUE_LETTER_SENT,
            },
            robot_kommentar=(
                "Anmodning om aktindsigt er sendt."
            ),
        )

        gem_pdf_som_sharepoint_vedhaeftning(
            item=item,
            pdf_bytes=pdf_bytes,
            file_name=(
                cfg.ARCHIVED_AUTHORITY_LETTER_FILE_NAME
            ),
        )

        set_state(
            item.data,
            item,
            States.MYNDIGHEDSBREV_GEMT_SHAREPOINT,
        )

        udsaet_til_naeste_dag(
            item=item,
            message=(
                "Afventer svar fra virksomhed eller myndighed."
            ),
        )

        return

    if mangler_state(
        item.data,
        States.MYNDIGHEDSBREV_GEMT_SHAREPOINT,
    ):
        regenerated_pdf = (
            opret_myndighedsbrev_pdf(
                sharepoint_data
            )
        )

        gem_pdf_som_sharepoint_vedhaeftning(
            item=item,
            pdf_bytes=regenerated_pdf,
            file_name=(
                cfg.REGENERATED_AUTHORITY_LETTER_FILE_NAME
            ),
        )

        gem_sharepoint_opdateringer(
            item=item,
            updates={},
            robot_kommentar=(
                "Vedhæftet PDF er regenereret i dag."
            ),
        )

        set_state(
            item.data,
            item,
            States.MYNDIGHEDSBREV_GEMT_SHAREPOINT,
        )

    await kontroller_myndighedsfrist(
        item=item,
        sharepoint_data=sharepoint_data,
    )

async def behandl_rykkerbrev(
    item,
    sharepoint_data: dict[str, Any],
) -> None:
    """
    Sender eller arkiverer rykkerbrevet.

    State 2.5 beskytter mod dobbelt afsendelse.
    State 2.7 viser, at PDF-kopien er gemt på SharePoint.
    """
    if mangler_state(
        item.data,
        States.RYKKERBREV_SENDT,
    ):
        reminder_pdf = opret_rykkerbrev_pdf(
            sharepoint_data
        )

        consent_file_name, consent_pdf_bytes = (
            hent_samtykke_som_pdf(
                item
            )
        )

        result = await send_rykkerbrev_digitalpost(
            item=item,
            reminder_pdf_bytes=reminder_pdf,
            consent_file_name=consent_file_name,
            consent_pdf_bytes=consent_pdf_bytes,
        )

        sent_at = datetime.now(
            timezone.utc
        )

        authority_data = (
            item.data
            .setdefault("box", {})
            .setdefault(
                "myndighedsbrev",
                {},
            )
        )

        authority_data[
            "reminder_forsendelses_id"
        ] = result.forsendelses_id

        authority_data[
            "reminder_sent_at"
        ] = sent_at.isoformat()

        update_item_data(
            item.data,
            item=item,
        )

        set_state(
            item.data,
            item,
            States.RYKKERBREV_SENDT,
        )

        gem_sharepoint_opdateringer(
            item=item,
            updates={
                cfg.FIELD_LETTER_STATUS:
                    cfg.VALUE_REMINDER_SENT,
            },
            robot_kommentar=(
                "Rykker om aktindsigt er sendt."
            ),
        )

        gem_pdf_som_sharepoint_vedhaeftning(
            item=item,
            pdf_bytes=reminder_pdf,
            file_name=(
                cfg.ARCHIVED_REMINDER_FILE_NAME
            ),
        )

        set_state(
            item.data,
            item,
            States.RYKKERBREV_GEMT_SHAREPOINT,
        )

        udsaet_til_naeste_dag(
            item=item,
            message=(
                "Afventer svar efter sendt rykker."
            ),
        )

        return

    if mangler_state(
        item.data,
        States.RYKKERBREV_GEMT_SHAREPOINT,
    ):
        regenerated_pdf = (
            opret_rykkerbrev_pdf(
                sharepoint_data
            )
        )

        gem_pdf_som_sharepoint_vedhaeftning(
            item=item,
            pdf_bytes=regenerated_pdf,
            file_name=(
                cfg.REGENERATED_REMINDER_FILE_NAME
            ),
        )

        gem_sharepoint_opdateringer(
            item=item,
            updates={},
            robot_kommentar=(
                "Vedhæftet rykker-PDF er regenereret i dag."
            ),
        )

        set_state(
            item.data,
            item,
            States.RYKKERBREV_GEMT_SHAREPOINT,
        )

    udsaet_til_naeste_dag(
        item=item,
        message=(
            "Afventer svar efter sendt rykker."
        ),
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

    Kun SharePoint-items med "Behandles af" = "Robot" behandles.

    Hvis "Behandles af" har en anden værdi:
        - procestrin udføres ikke
        - signeringsstatus kontrolleres ikke
        - ATS-itemet udsættes til næste dag

    Første kørsel:
        - generér brev
        - opret signeringsflow
        - gem ikke-signeret PDF på SharePoint
        - sæt state 1.0
        - udsæt én dag

    Senere kørsel:
        - se at state 1.0 findes
        - spring dokumentgenereringen over
        - kontrollér det eksisterende signeringsflow
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

        # ----------------------------------------------------
        # KUN ITEMS MARKERET TIL ROBOT
        # ----------------------------------------------------

        if not skal_behandles_af_robot(
            sharepoint_data
        ):
            handled_by = str(
                sharepoint_data.get(
                    cfg.FIELD_HANDLED_BY
                )
                or ""
            ).strip()

            logger.info(
                "Item %s behandles ikke, fordi "
                "%r er sat til %r og ikke %r.",
                item.reference,
                cfg.FIELD_HANDLED_BY,
                handled_by,
                cfg.VALUE_ROBOT,
            )

            udsaet_til_naeste_dag(
                item=item,
                message=(
                    "SharePoint-itemet er ikke markeret "
                    "til robotbehandling."
                ),
            )

            return

        # ----------------------------------------------------
        # GENSTART: STATE 1.0 FINDES
        # ----------------------------------------------------

        if not mangler_state(
            data,
            States.BORGERBREV_SENDT_TIL_SIGNERING,
        ):
            logger.info(
                "State %r findes allerede. "
                "Datafordeleren, Word, Gotenberg og oprettelse "
                "af nyt signeringsflow springes over.",
                States.BORGERBREV_SENDT_TIL_SIGNERING,
            )

            kontroller_eksisterende_signeringsflow(
                item
            )

            return

        # ----------------------------------------------------
        # FLOW_ID FINDES UDEN STATE 1.0
        # ----------------------------------------------------

        signing_data = (
            data
            .get("box", {})
            .get("signeringsportal")
        )

        if isinstance(signing_data, dict):
            existing_flow_id = str(
                signing_data.get("flow_id") or ""
            ).strip()

            if existing_flow_id:
                logger.warning(
                    "Der findes et flow_id uden state 1.0. "
                    "Det eksisterende flow genbruges."
                )

                # Hvis uploaden af den ikke-signerede PDF fejlede
                # i forrige kørsel, har processen ikke længere
                # PDF-bytes. Derfor kontrolleres det eksisterende
                # flow uden at oprette et nyt.
                set_state(
                    data,
                    item,
                    States.BORGERBREV_SENDT_TIL_SIGNERING,
                )

                kontroller_eksisterende_signeringsflow(
                    item
                )

                return

        # ----------------------------------------------------
        # FØRSTE KØRSEL
        # ----------------------------------------------------

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
        logger.warning(
            "Sagen kan ikke behandles automatisk: %s",
            error,
        )

        aflever_til_sagsbehandler(
            item=item,
            message=str(error),
        )