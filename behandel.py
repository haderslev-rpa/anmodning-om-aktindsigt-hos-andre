"""Orkestrering af procestrin. States og statekontrol bor kun her."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from automation_server_client import WorkItemStatus
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from q_sharepoint_api.functionality.sp_list_item_ats_box import (
    get_to_ats_box,
    update_from_ats_box,
)

import configuration as cfg
import brev_til_borger as borger
import brev_til_virksomhed_myndighed as myndighed

logger = logging.getLogger(__name__)


class States:
    BORGERBREV_GENERERET = "0.5 Brev til borger genereret"
    BORGERBREV_SENDT_TIL_SIGNERING = "1.0 Brev sendt til borger og afventer signering"
    DIGITALT_SAMTYKKE_MODTAGET = "1.5 Digitalt samtykke modtaget"
    DIGITALT_SAMTYKKE_GEMT_SHAREPOINT = "1.6 Signeret samtykke gemt i SharePoint"
    MYNDIGHEDSBREV_SENDT = "2.0 Sendt brev til virksomhed-myndighed"
    MYNDIGHEDSBREV_GEMT_SHAREPOINT = "2.2 Brev til virksomhed-myndighed gemt på SharePoint"
    RYKKERBREV_SENDT = "2.5 Rykkerbrev sendt til virksomhed-myndighed"
    RYKKERBREV_GEMT_SHAREPOINT = "2.7 Rykkerbrev gemt på SharePoint"


def mangler_state(data: dict[str, Any], state: str) -> bool:
    states = data.get("state", [])
    return not isinstance(states, list) or not any(
        isinstance(value, str) and state in value for value in states
    )


def set_state(data: dict[str, Any], item, state: str) -> None:
    update_item_data(data, item=item, state=state)


def required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"Oplysningerne mangler feltet {key!r}.")
    return value


def parse_utc_datetime(value: object, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Tidspunktet {field_name!r} mangler.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"Tidspunktet {field_name!r} er ugyldigt: {value!r}.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hent_aktuelle_sharepoint_data(item) -> dict[str, Any]:
    existing = item.data.get("box", {}).get("sharepoint")
    if not isinstance(existing, dict):
        raise ValueError("ATS-itemet mangler box.sharepoint.")
    result = get_to_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        list_item_id=required_text(existing, cfg.FIELD_ID),
        item=item,
    )
    update_item_data(item.data, item=item)
    return result


def gem_sharepoint_opdateringer(
    item,
    updates: dict[str, Any],
    *,
    robot_kommentar: str | None = None,
) -> dict[str, Any]:
    sharepoint_data = item.data.get("box", {}).get("sharepoint")
    if not isinstance(sharepoint_data, dict):
        raise ValueError("ATS-itemet mangler box.sharepoint.")
    item_id = required_text(sharepoint_data, cfg.FIELD_ID)
    sharepoint_data.update(updates)
    update_from_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item=item,
        robot_kommentar=robot_kommentar,
    )
    fresh = get_to_ats_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        list_item_id=item_id,
        item=item,
    )
    update_item_data(item.data, item=item)
    return fresh


def udsaet_til_naeste_dag(item, message: str) -> None:
    defer = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    update_item_data(item.data, item=item, defer=defer)
    item.update_status(WorkItemStatus.PENDING_USER_ACTION.value, f"{message} Næste kontrol: {defer}")


def aflever_til_sagsbehandler(item, message: str) -> None:
    gem_sharepoint_opdateringer(
        item,
        {cfg.FIELD_HANDLED_BY: cfg.VALUE_CASEWORKER},
        robot_kommentar=message,
    )
    item.update_status(WorkItemStatus.PENDING_USER_ACTION.value, message)


def _skal_behandles_af_robot(sharepoint_data: dict[str, Any]) -> bool:
    return str(sharepoint_data.get(cfg.FIELD_HANDLED_BY) or "").strip().casefold() == str(cfg.VALUE_ROBOT).casefold()


def _signeringsfrist_udloebet(signing_data: dict[str, Any], now: datetime) -> bool:
    created = parse_utc_datetime(signing_data.get("created_at"), "signeringsportal.created_at")
    return now >= created + timedelta(days=cfg.SIGNING_EXPIRY_DAYS)


def _kontroller_signering(item) -> None:
    signing_data = item.data.get("box", {}).get("signeringsportal")
    if not isinstance(signing_data, dict):
        raise ValueError("ATS-itemet mangler box.signeringsportal.")
    flow_id = required_text(signing_data, "flow_id")
    status = borger.hent_signeringsstatus(flow_id)
    now = datetime.now(timezone.utc)
    signing_data.update({"status": status.status, "updated_at": now.isoformat()})
    update_item_data(item.data, item=item)

    if status.er_signeret:
        pdf = borger.hent_signeret_pdf(flow_id)
        if mangler_state(item.data, States.DIGITALT_SAMTYKKE_GEMT_SHAREPOINT):
            borger.gem_pdf_paa_sharepoint(item, pdf, cfg.SIGNED_CONSENT_FILE_NAME)
            set_state(item.data, item, States.DIGITALT_SAMTYKKE_GEMT_SHAREPOINT)
        if mangler_state(item.data, States.DIGITALT_SAMTYKKE_MODTAGET):
            set_state(item.data, item, States.DIGITALT_SAMTYKKE_MODTAGET)
        gem_sharepoint_opdateringer(
            item,
            {cfg.FIELD_CONSENT: cfg.VALUE_YES},
            robot_kommentar="Signeret samtykke er modtaget og vedhæftet.",
        )
        return

    if status.afventer:
        if _signeringsfrist_udloebet(signing_data, now):
            aflever_til_sagsbehandler(
                item,
                f"Samtykket er ikke underskrevet inden for {cfg.SIGNING_EXPIRY_DAYS} dage.",
            )
        else:
            udsaet_til_naeste_dag(item, "Afventer borgerens digitale signering.")
        return

    if status.er_afsluttet_uden_signatur:
        aflever_til_sagsbehandler(item, f"Signeringsforløbet sluttede uden underskrift. Status: {status.status}.")
        return
    raise RuntimeError(f"Ukendt signeringsstatus: {status.status!r}.")


def _send_borgerbrev(item, sharepoint_data: dict[str, Any]) -> None:
    signing_data = item.data.get("box", {}).get("signeringsportal")
    if isinstance(signing_data, dict) and str(signing_data.get("flow_id") or "").strip():
        if mangler_state(item.data, States.BORGERBREV_SENDT_TIL_SIGNERING):
            regenerated = borger.opret_borgerbrev_pdf(sharepoint_data)
            borger.gem_pdf_paa_sharepoint(item, regenerated, cfg.UNSIGNED_CONSENT_FILE_NAME)
            gem_sharepoint_opdateringer(item, {}, robot_kommentar="Ikke-signeret PDF er regenereret og vedhæftet.")
            set_state(item.data, item, States.BORGERBREV_SENDT_TIL_SIGNERING)
            udsaet_til_naeste_dag(item, "Afventer digital signering.")
        else:
            _kontroller_signering(item)
        return

    pdf = borger.opret_borgerbrev_pdf(sharepoint_data)
    if mangler_state(item.data, States.BORGERBREV_GENERERET):
        set_state(item.data, item, States.BORGERBREV_GENERERET)
    flow = borger.opret_signeringsflow(required_text(sharepoint_data, cfg.FIELD_CPR), pdf)
    update_item_data(item.data, box_updates={"signeringsportal": flow}, item=item)
    borger.gem_pdf_paa_sharepoint(item, pdf, cfg.UNSIGNED_CONSENT_FILE_NAME)
    gem_sharepoint_opdateringer(item, {}, robot_kommentar="Brev er sendt til signering. Vedhæftet PDF er ikke signeret.")
    set_state(item.data, item, States.BORGERBREV_SENDT_TIL_SIGNERING)
    udsaet_til_naeste_dag(item, "Borgerbrev er sendt til signering.")


def _myndighedsdata(item) -> dict[str, Any]:
    return item.data.setdefault("box", {}).setdefault("myndighedsbrev", {})


async def _send_myndighedsbrev(item, sharepoint_data: dict[str, Any]) -> None:
    if mangler_state(item.data, States.MYNDIGHEDSBREV_SENDT):
        letter = myndighed.opret_myndighedsbrev_pdf(sharepoint_data)
        consent_name, consent = myndighed.hent_samtykke_som_pdf(item)
        result = await myndighed.send_digitalpost(
            item=item,
            letter_pdf=letter,
            letter_file_name=cfg.MYNDIGHEDSBREV_PDF_FILE_NAME,
            subject="Anmodning om aktindsigt",
            consent_file_name=consent_name,
            consent_pdf=consent,
        )
        sent_at = datetime.now(timezone.utc).isoformat()
        update_item_data(item.data, box_updates={"myndighedsbrev": {
            "forsendelses_id": result.forsendelses_id,
            "sent_at": sent_at,
            "reminder_sent_at": None,
        }}, item=item)
        set_state(item.data, item, States.MYNDIGHEDSBREV_SENDT)
        gem_sharepoint_opdateringer(
            item,
            {cfg.FIELD_LETTER_STATUS: cfg.VALUE_LETTER_SENT},
            robot_kommentar="Anmodning om aktindsigt er sendt.",
        )
        try:
            myndighed.gem_pdf_paa_sharepoint(item, letter, cfg.ARCHIVED_AUTHORITY_LETTER_FILE_NAME)
            set_state(item.data, item, States.MYNDIGHEDSBREV_GEMT_SHAREPOINT)
        except Exception:
            logger.exception("Brevet blev sendt, men kunne ikke gemmes på SharePoint.")
        udsaet_til_naeste_dag(item, "Afventer svar fra virksomhed eller myndighed.")
        return

    if mangler_state(item.data, States.MYNDIGHEDSBREV_GEMT_SHAREPOINT):
        regenerated = myndighed.opret_myndighedsbrev_pdf(sharepoint_data)
        myndighed.gem_pdf_paa_sharepoint(item, regenerated, cfg.REGENERATED_AUTHORITY_LETTER_FILE_NAME)
        gem_sharepoint_opdateringer(item, {}, robot_kommentar="Vedhæftet myndighedsbrev er regenereret i dag.")
        set_state(item.data, item, States.MYNDIGHEDSBREV_GEMT_SHAREPOINT)

    await _kontroller_myndighedsfrist(item, sharepoint_data)


async def _send_rykker(item, sharepoint_data: dict[str, Any]) -> None:
    if mangler_state(item.data, States.RYKKERBREV_SENDT):
        reminder = myndighed.opret_rykkerbrev_pdf(sharepoint_data)
        consent_name, consent = myndighed.hent_samtykke_som_pdf(item)
        result = await myndighed.send_digitalpost(
            item=item,
            letter_pdf=reminder,
            letter_file_name=cfg.RYKKERBREV_PDF_FILE_NAME,
            subject="Rykker vedrørende anmodning om aktindsigt",
            consent_file_name=consent_name,
            consent_pdf=consent,
        )
        data = _myndighedsdata(item)
        data.update({
            "reminder_forsendelses_id": result.forsendelses_id,
            "reminder_sent_at": datetime.now(timezone.utc).isoformat(),
        })
        update_item_data(item.data, item=item)
        set_state(item.data, item, States.RYKKERBREV_SENDT)
        gem_sharepoint_opdateringer(
            item,
            {cfg.FIELD_LETTER_STATUS: cfg.VALUE_REMINDER_SENT},
            robot_kommentar="Rykker om aktindsigt er sendt.",
        )
        try:
            myndighed.gem_pdf_paa_sharepoint(item, reminder, cfg.ARCHIVED_REMINDER_FILE_NAME)
            set_state(item.data, item, States.RYKKERBREV_GEMT_SHAREPOINT)
        except Exception:
            logger.exception("Rykkeren blev sendt, men kunne ikke gemmes på SharePoint.")
        udsaet_til_naeste_dag(item, "Afventer svar efter sendt rykker.")
        return

    if mangler_state(item.data, States.RYKKERBREV_GEMT_SHAREPOINT):
        regenerated = myndighed.opret_rykkerbrev_pdf(sharepoint_data)
        myndighed.gem_pdf_paa_sharepoint(item, regenerated, cfg.REGENERATED_REMINDER_FILE_NAME)
        gem_sharepoint_opdateringer(item, {}, robot_kommentar="Vedhæftet rykkerbrev er regenereret i dag.")
        set_state(item.data, item, States.RYKKERBREV_GEMT_SHAREPOINT)
    udsaet_til_naeste_dag(item, "Afventer svar efter sendt rykker.")


async def _kontroller_myndighedsfrist(item, sharepoint_data: dict[str, Any]) -> None:
    if str(sharepoint_data.get(cfg.FIELD_CASE_FILE_RECEIVED) or "").strip().casefold() == cfg.VALUE_YES.casefold():
        logger.info("SharePoint viser, at sagsakter er modtaget.")
        return
    data = _myndighedsdata(item)
    now = datetime.now(timezone.utc)
    sent_at = parse_utc_datetime(data.get("sent_at"), "myndighedsbrev.sent_at")

    if mangler_state(item.data, States.RYKKERBREV_SENDT):
        if now >= sent_at + timedelta(days=cfg.AUTHORITY_RESPONSE_DAYS):
            await _send_rykker(item, sharepoint_data)
        else:
            udsaet_til_naeste_dag(item, "Afventer svar fra virksomhed eller myndighed.")
        return

    reminder_at = parse_utc_datetime(data.get("reminder_sent_at"), "myndighedsbrev.reminder_sent_at")
    if now >= reminder_at + timedelta(days=cfg.AUTHORITY_REMINDER_RESPONSE_DAYS):
        aflever_til_sagsbehandler(item, "Der er ikke modtaget sagsakter efter anmodning og rykker.")
    else:
        await _send_rykker(item, sharepoint_data)


async def behandel_page(item, session, page) -> None:
    del session, page
    if not isinstance(item.data, dict):
        raise TypeError("item.data skal være en dictionary.")
    try:
        sharepoint_data = hent_aktuelle_sharepoint_data(item)
        if not _skal_behandles_af_robot(sharepoint_data):
            udsaet_til_naeste_dag(item, "SharePoint-itemet er ikke markeret til robotbehandling.")
            return

        consent = required_text(sharepoint_data, cfg.FIELD_CONSENT)
        if consent.casefold() == cfg.VALUE_YES.casefold():
            await _send_myndighedsbrev(item, sharepoint_data)
            return

        if not mangler_state(item.data, States.BORGERBREV_SENDT_TIL_SIGNERING):
            _kontroller_signering(item)
            return

        if consent.casefold() == cfg.VALUE_NO.casefold():
            _send_borgerbrev(item, sharepoint_data)
            return

        logger.info("Samtykkestatus %r udløser ingen automatisk handling.", consent)
    except ValueError as error:
        logger.warning("Sagen kan ikke behandles automatisk: %s", error)
        aflever_til_sagsbehandler(item, str(error))
