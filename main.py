import asyncio
import logging
import os
import sys
from automation_server_client import AutomationServer, WorkItemError, WorkItemStatus, Workqueue
from behandel import behandel_page
from mailrobot import run_mailrobot
from queue_producer import populate_queue
from q_haderslev_vbo.playwright.browser_session import BrowserSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("automation_server_client").setLevel(logging.WARNING)
logging.getLogger("debugpy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

def get_headless_flag() -> bool:
    return os.getenv("HEADLESS", "true").lower() == "true"

def _vaelg_items_til_behandling(workqueue: Workqueue):
    reference = os.getenv("DEBUG_ITEM_REFERENCE", "").strip()
    if not reference:
        return workqueue
    items = workqueue.get_item_by_reference(reference=reference, status=WorkItemStatus.NEW)
    if not items:
        raise RuntimeError(f"Ingen NEW-items fundet med reference: {reference}")
    items[0].update_status(WorkItemStatus.IN_PROGRESS.value, "Startet via lokal debugkørsel")
    return [items[0]]

async def process_workqueue(workqueue: Workqueue, debug: bool):
    session = BrowserSession(headless=get_headless_flag(), debug=debug)
    await session.start()
    page = await session.new_page()
    try:
        for item in _vaelg_items_til_behandling(workqueue):
            with item:
                try:
                    await behandel_page(item=item, session=session, page=page)
                    item.update(item.data)
                except WorkItemError as error:
                    logger.error("WorkItemError for %s: %s", item.reference, error)
                    item.fail(str(error))
                except Exception:
                    logger.exception("Uventet fejl for item %s", item.reference)
                    if session.context and session.context.pages:
                        await session.screenshot(session.context.pages[-1], "hard_exception", always=True)
                    raise
    finally:
        await session.close()

if __name__ == "__main__":
    debug = "--debug" in sys.argv
    queue_mode = "--queue" in sys.argv
    mail_mode = "--mail" in sys.argv
    automation_server = AutomationServer.from_environment()
    if mail_mode:
        print(f"Behandlede mails: {run_mailrobot()}")
    else:
        workqueue = automation_server.workqueue()
        if queue_mode:
            asyncio.run(populate_queue(workqueue=workqueue, debug=debug))
        else:
            asyncio.run(process_workqueue(workqueue=workqueue, debug=debug))
