"""Modtag svarmails via q-outlook-api og gem dem på SharePoint.

Filen indeholder ingen egne Microsoft Graph-kald og henter ingen credentials.
Outlook håndteres af q-outlook-api, og SharePoint håndteres af q-sharepoint-api.
"""
from __future__ import annotations

from datetime import datetime
import re
from tempfile import TemporaryDirectory
from pathlib import Path
from zoneinfo import ZoneInfo

import configuration as cfg
from q_outlook_api.functionality.mail_api import delete_mail, download_mail, get_mails
from q_sharepoint_api.functionality.sp_list_attachments import add_attachment_to_item
from q_sharepoint_api.functionality.sp_list_item_save import save_list_item
from q_sharepoint_api.functionality.sp_list_items import get_list_items


def extract_sharepoint_id(body_text: str) -> str:
    """Læs SharePoint-id blandt de første konfigurerede tegn i mailteksten."""
    prefix = str(body_text or "")[: cfg.MAIL_ID_PREFIX_LENGTH]
    digits = "".join(character for character in prefix if character.isdigit())
    if not digits:
        raise ValueError(
            "Mailens begyndelse indeholder ikke et gyldigt SharePoint-id."
        )
    return digits


def _safe_subject(subject: str) -> str:
    """Lav et sikkert og læsbart filnavn fra mailens emne."""
    value = re.sub(
        r"[^A-Za-z0-9ÆØÅæøå_-]+",
        "_",
        str(subject or "mail"),
    ).strip("_")
    return value[:80] or "mail"


def _assert_sharepoint_item_exists(item_id: str) -> None:
    """Stop tydeligt, hvis mailen peger på et ukendt SharePoint-item."""
    result = get_list_items(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=item_id,
    )
    if result.get("count") != 1:
        raise LookupError(
            f"Forventede ét SharePoint-item med id {item_id}, "
            f"men fandt {result.get('count', 0)}."
        )


def _download_and_upload_mail(item_id: str, message: dict) -> str:
    """Hent hele mailen som EML og upload den på SharePoint-itemet."""
    message_id = str(message.get("message_id") or message.get("id") or "").strip()
    if not message_id:
        raise ValueError("Mailen mangler message_id fra q-outlook-api.")

    downloaded = download_mail(
        user_mail=cfg.MAILBOX_ADDRESS,
        message_id=message_id,
    )
    if not downloaded.get("exists"):
        raise LookupError(f"Mailen findes ikke længere i Outlook: {message_id}")

    content = downloaded.get("content_bytes")
    if not isinstance(content, bytes) or not content:
        raise ValueError("download_mail returnerede ikke gyldige mail-bytes.")

    timestamp = datetime.now(ZoneInfo("Europe/Copenhagen")).strftime(
        "%Y%m%d_%H%M%S"
    )
    file_name = f"{timestamp}_{_safe_subject(message.get('subject'))}.eml"

    # q-sharepoint-api modtager aktuelt file_path ved listevedhæftninger.
    # Tempfilen findes kun under upload og slettes straks derefter.
    with TemporaryDirectory() as folder:
        path = Path(folder) / file_name
        path.write_bytes(content)
        add_attachment_to_item(
            site_name=cfg.SHAREPOINT_SITE_NAME,
            list_name=cfg.SHAREPOINT_LIST_NAME,
            item_id=item_id,
            file_path=str(path),
        )

    return file_name
def _update_sharepoint_item(item_id: str) -> None:
    """Sæt SharePoint-itemet tilbage til robotbehandling."""
    save_list_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        data={
            "id": item_id,
            cfg.FIELD_CASE_FILE_RECEIVED: cfg.VALUE_YES,
            cfg.FIELD_HANDLED_BY: cfg.VALUE_ROBOT,
        },
    )


def run_mailrobot() -> int:
    """Behandl mails i indbakken og returnér antal færdige mails."""
    if not cfg.MAIL_ROBOT_ENABLED:
        print("Mailrobotten er slået fra i configuration.py.")
        return 0

    messages = get_mails(
        user_mail=cfg.MAILBOX_ADDRESS,
        folder="inbox",
        limit=cfg.MAIL_TOP,
        include_attachments=False,
        prefer_plain_text=True,
    )

    processed = 0
    for message in messages:
        item_id = extract_sharepoint_id(message.get("body") or "")
        _assert_sharepoint_item_exists(item_id)
        _download_and_upload_mail(item_id, message)
        _update_sharepoint_item(item_id)

        if cfg.MAIL_DELETE_AFTER_SUCCESS:
            delete_mail(
                user_mail=cfg.MAILBOX_ADDRESS,
                message_id=message["message_id"],
                permanent_delete=False,
            )

        processed += 1

    return processed


if __name__ == "__main__":
    print(f"Behandlede mails: {run_mailrobot()}")

