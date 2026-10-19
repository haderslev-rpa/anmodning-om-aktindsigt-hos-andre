"""Samler SharePoint-kald, så proceslogikken kan testes separat."""
from pathlib import Path
from tempfile import TemporaryDirectory
import configuration as cfg
from models import BinaryFile
from q_sharepoint_api.functionality.sp_documents import download_document, get_documents
from q_sharepoint_api.functionality.sp_list_attachments import add_attachment_to_item, download_attachment, list_attachments_for_item

class SharePointGateway:
    def get_template(self, *, file_name: str) -> bytes:
        result = get_documents(
            site_name=cfg.SHAREPOINT_SITE_NAME,
            library_name=cfg.SHAREPOINT_TEMPLATE_LIBRARY_NAME,
            filters={"file_name": file_name},
            page_size=10,
            max_items=2,
        )
        documents = result.get("documents") or []
        if len(documents) != 1:
            raise RuntimeError(f"Forventede præcis én Word-skabelon med navnet {file_name!r}, men fandt {len(documents)}.")
        return download_document(
            site_name=cfg.SHAREPOINT_SITE_NAME,
            library_name=cfg.SHAREPOINT_TEMPLATE_LIBRARY_NAME,
            drive_item_id=documents[0]["drive_item_id"],
        )

    def list_attachments(self, *, item_id: int) -> list[dict]:
        return list_attachments_for_item(cfg.SHAREPOINT_SITE_NAME, cfg.SHAREPOINT_LIST_NAME, item_id)

    def download_list_attachment(self, *, attachment: dict) -> BinaryFile:
        with TemporaryDirectory() as folder:
            path = Path(download_attachment(cfg.SHAREPOINT_SITE_NAME, attachment, folder))
            return BinaryFile(path.name, path.read_bytes())

    def add_list_attachment(self, *, item_id: int, document: BinaryFile) -> dict:
        with TemporaryDirectory() as folder:
            path = Path(folder) / document.file_name
            path.write_bytes(document.content)
            return add_attachment_to_item(cfg.SHAREPOINT_SITE_NAME, cfg.SHAREPOINT_LIST_NAME, item_id, str(path))
