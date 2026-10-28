"""Mailrobot via Microsoft Graph.

Graph eksporterer mailen som MIME. Derfor gemmes testmailen som .eml, ikke som en
falsk .msg-fil. Både .eml og .msg accepteres senere som sagsaktfiler.
"""
from datetime import datetime
from html import unescape
import re
from zoneinfo import ZoneInfo
import requests
import configuration as cfg
from models import BinaryFile
from q_sharepoint_api.sp_api import get_client
from sharepoint_gateway import SharePointGateway

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

def extract_sharepoint_id(body_text: str) -> str:
    prefix = (body_text or "")[:cfg.MAIL_ID_PREFIX_LENGTH]
    digits = "".join(character for character in prefix if character.isdigit())
    if not digits:
        raise ValueError("De første 5 tegn i mailteksten indeholder ikke et SharePoint-id.")
    return digits

def _plain_text(body: dict) -> str:
    content = unescape(str(body.get("content") or ""))
    return re.sub(r"<[^>]+>", "", content).strip()

def _safe_subject(subject: str) -> str:
    value = re.sub(r"[^A-Za-z0-9ÆØÅæøå_-]+", "_", subject or "mail").strip("_")
    return value[:80] or "mail"

def run_mailrobot() -> int:
    if not cfg.MAIL_ROBOT_ENABLED:
        print("Mailrobotten er slået fra i configuration.py.")
        return 0
    client = get_client()
    headers = client.auth.graph_headers()
    url = f"{GRAPH_ROOT}/users/{cfg.MAILBOX_ADDRESS}/mailFolders/inbox/messages?$top={cfg.MAIL_TOP}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    gateway = SharePointGateway()
    processed = 0
    for message in response.json().get("value", []):
        item_id = extract_sharepoint_id(_plain_text(message.get("body") or {}))
        gateway.get_item(item_id)
        message_id = message["id"]
        mime_response = requests.get(
            f"{GRAPH_ROOT}/users/{cfg.MAILBOX_ADDRESS}/messages/{message_id}/$value",
            headers=headers,
            timeout=60,
        )
        mime_response.raise_for_status()
        timestamp = datetime.now(ZoneInfo("Europe/Copenhagen")).strftime("%Y%m%d_%H%M%S")
        file_name = f"{timestamp}_{_safe_subject(message.get('subject', 'mail'))}{cfg.MAIL_FILE_EXTENSION}"
        gateway.add_attachment(item_id, BinaryFile(file_name, mime_response.content, "message/rfc822"))
        gateway.update_item(item_id, {
            cfg.FIELD_CASE_FILE_RECEIVED: cfg.VALUE_YES,
            cfg.FIELD_HANDLED_BY: cfg.VALUE_ROBOT,
        })
        if cfg.MAIL_DELETE_AFTER_SUCCESS:
            delete_response = requests.delete(
                f"{GRAPH_ROOT}/users/{cfg.MAILBOX_ADDRESS}/messages/{message_id}",
                headers=headers,
                timeout=30,
            )
            if delete_response.status_code not in {200, 202, 204}:
                delete_response.raise_for_status()
        processed += 1
    return processed

if __name__ == "__main__":
    print(f"Behandlede mails: {run_mailrobot()}")
