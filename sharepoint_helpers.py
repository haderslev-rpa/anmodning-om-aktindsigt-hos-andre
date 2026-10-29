"""Små proceshjælpere omkring de offentlige q-sharepoint-api-funktioner.

Modulet indeholder ingen klient, credentials, URL'er eller HTTP-kald.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import configuration as cfg
from models import BinaryFile
from q_sharepoint_api.functionality.sp_documents import (
    download_document,
    get_documents,
)
from q_sharepoint_api.functionality.sp_list_attachments import (
    add_attachment_to_item,
    download_attachment,
    list_attachments_for_item,
)


def get_template(file_name: str) -> bytes:
    """Hent præcis én Word-skabelon og returnér filen som bytes."""
    result = get_documents(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        library_name=cfg.SHAREPOINT_TEMPLATE_LIBRARY_NAME,
        filters={"file_name": file_name},
        page_size=10,
        max_items=2,
    )
    documents = result.get("documents") or []
    if len(documents) != 1:
        raise RuntimeError(
            f"Forventede én Word-skabelon med navnet {file_name!r}, "
            f"men fandt {len(documents)}."
        )
    return download_document(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        library_name=cfg.SHAREPOINT_TEMPLATE_LIBRARY_NAME,
        drive_item_id=documents[0]["drive_item_id"],
    )


def list_item_attachments(item_id: str | int) -> list[dict]:
    """Returnér metadata for et listeitems vedhæftninger."""
    return list_attachments_for_item(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        item_id=item_id,
    )


def download_list_attachment(attachment: dict) -> BinaryFile:
    """Download én listevedhæftning og returnér navn og bytes."""
    with TemporaryDirectory() as folder:
        local_path = Path(
            download_attachment(
                site_name=cfg.SHAREPOINT_SITE_NAME,
                attachment=attachment,
                target_folder=folder,
            )
        )
        return BinaryFile(local_path.name, local_path.read_bytes())


def add_list_attachment(item_id: str | int, document: BinaryFile) -> dict:
    """Upload en BinaryFile til et SharePoint-listeitem."""
    with TemporaryDirectory() as folder:
        local_path = Path(folder) / document.file_name
        local_path.write_bytes(document.content)
        return add_attachment_to_item(
            site_name=cfg.SHAREPOINT_SITE_NAME,
            list_name=cfg.SHAREPOINT_LIST_NAME,
            item_id=item_id,
            file_path=str(local_path),
        )
