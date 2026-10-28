import logging
import configuration as cfg
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from sharepoint_gateway import SharePointGateway

logger = logging.getLogger(__name__)

async def populate_queue(workqueue, debug: bool):
    gateway = SharePointGateway()
    for sharepoint_item in gateway.get_items():
        if sharepoint_item.get(cfg.FIELD_HANDLED_BY) != cfg.VALUE_ROBOT:
            continue
        reference = f"sharepoint-{sharepoint_item[cfg.FIELD_ID]}"
        existing = workqueue.get_item_by_reference(reference=reference)
        if existing:
            logger.info("ATS-item findes allerede: %s", reference)
            continue
        data = {}
        update_item_data(data, box_updates={"sharepoint": sharepoint_item}, update=False)
        workqueue.add_item(data=data, reference=reference)
