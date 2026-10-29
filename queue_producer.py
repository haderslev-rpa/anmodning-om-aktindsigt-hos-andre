"""Producer: opret ATS-items fra SharePoint-listen."""
import logging

import configuration as cfg
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from q_sharepoint_api.functionality.sp_list_items import get_list_items

logger = logging.getLogger(__name__)


async def populate_queue(workqueue, debug: bool) -> None:
    """Opret ét ATS-item pr. SharePoint-item, som står til Robot."""
    result = get_list_items(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
    )

    for sharepoint_item in result.get("items", []):
        if sharepoint_item.get(cfg.FIELD_HANDLED_BY) != cfg.VALUE_ROBOT:
            continue

        reference = f"sharepoint-{sharepoint_item[cfg.FIELD_ID]}"
        existing = workqueue.get_item_by_reference(reference=reference)
        if existing:
            logger.info("ATS-item findes allerede: %s", reference)
            continue

        data = {}
        update_item_data(
            data,
            box_updates={"sharepoint": sharepoint_item},
            update=False,
        )
        workqueue.add_item(data=data, reference=reference)
