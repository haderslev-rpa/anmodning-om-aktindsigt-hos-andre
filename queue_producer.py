"""Opretter work items fra SharePoint-listen."""
import logging
import configuration as cfg
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from q_sharepoint_api.functionality.sp_list_items import get_list_items

logger = logging.getLogger(__name__)

async def populate_queue(workqueue, debug: bool):
    sharepoint_items = get_list_items(cfg.SHAREPOINT_SITE_NAME, cfg.SHAREPOINT_LIST_NAME)
    for sp in sharepoint_items:
        if sp.get(cfg.FIELD_HANDLED_BY) != cfg.VALUE_ROBOT:
            continue
        reference = f"sharepoint-{sp[cfg.FIELD_ID]}"
        if workqueue.get_item_by_reference(reference=reference):
            logger.info("Work item findes allerede: %s", reference)
            continue
        data = {}
        update_item_data(data, box_updates={"sharepoint": sp}, update=False)
        workqueue.add_item(data=data, reference=reference)
