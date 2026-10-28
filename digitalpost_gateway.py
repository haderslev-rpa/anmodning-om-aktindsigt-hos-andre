import configuration as cfg
from models import BinaryFile
from q_digitalpost.digital_post import DigitalPost
from q_digitalpost.models import DeliveryMethod, DigitalPostAttachment

class DigitalPostGateway:
    async def send(self, *, main_document: BinaryFile, attachments: list[BinaryFile], cvr: str, p_number: str, production_unit_name: str, subject: str, source_item_id: str | int) -> dict:
        method = DeliveryMethod(cfg.DIGITAL_POST_DELIVERY_METHOD)
        result = await DigitalPost().send_digital_post(
            pdf_content=main_document.content,
            file_name=main_document.file_name,
            cpr_or_cvr=cvr,
            subject=subject,
            source_process=cfg.DIGITAL_POST_SOURCE_PROCESS,
            source_item_id=source_item_id,
            delivery_method=method,
            attachments=[DigitalPostAttachment(content=file.content, file_name=file.file_name) for file in attachments],
            p_number=p_number,
            production_unit_name=production_unit_name,
        )
        return {
            "forsendelses_id": result.forsendelses_id,
            "queue_id": result.queue_id,
            "document_count": result.document_count,
            "status": result.status,
            "reason": result.reason,
            "message": result.message,
        }
