import asyncio
import logging
import os
import sys

from automation_server_client import (
    AutomationServer,
    WorkItemError,
    WorkItemStatus,
    Workqueue,
)
from q_haderslev_vbo.playwright.browser_session import BrowserSession


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("automation_server_client").setLevel(logging.WARNING)
logging.getLogger("debugpy").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# HJÆLPEFUNKTIONER
# ------------------------------------------------------------
def get_headless_flag() -> bool:
    """Læs HEADLESS fra .env. Standard er true."""
    return os.getenv("HEADLESS", "true").lower() == "true"


def _vaelg_items_til_behandling(workqueue: Workqueue):
    """Vælg hele køen eller ét bestemt NEW-item ved lokal debug."""
    reference = os.getenv("DEBUG_ITEM_REFERENCE", "").strip()

    if not reference:
        return workqueue

    items = workqueue.get_item_by_reference(
        reference=reference,
        status=WorkItemStatus.NEW,
    )

    if not items:
        raise RuntimeError(
            f"Ingen NEW-items fundet med reference: {reference}"
        )

    item = items[0]
    item.update_status(
        WorkItemStatus.IN_PROGRESS.value,
        "Startet via lokal debugkørsel",
    )

    return [item]


# ------------------------------------------------------------
# QUEUE-MODE (PRODUCER)
# ------------------------------------------------------------
async def run_queue_mode(
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Opret work items fra SharePoint-listen."""

    # Importeres først efter AutomationServer.from_environment().
    # Dermed indlæses behandel.py og dokumentintegrationerne ikke
    # under --queue.
    from queue_producer import populate_queue

    logger.info("Queue mode startet.")

    await populate_queue(
        workqueue=workqueue,
        debug=debug,
    )


# ------------------------------------------------------------
# PROCESS-MODE (WORKER)
# ------------------------------------------------------------
async def process_workqueue(
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Behandl work items ét ad gangen."""

    # Importeres først efter AutomationServer.from_environment().
    from behandel import behandel_page

    logger.info("Worker mode startet. debug=%s", debug)

    session = BrowserSession(
        headless=get_headless_flag(),
        debug=debug,
    )

    await session.start()
    page = await session.new_page()

    try:
        for item in _vaelg_items_til_behandling(workqueue):
            with item:
                try:
                    logger.info(
                        "Behandler item id=%s reference=%s",
                        item.id,
                        item.reference,
                    )

                    await behandel_page(
                        item=item,
                        session=session,
                        page=page,
                    )

                    item.update(item.data)

                except WorkItemError as error:
                    logger.error(
                        "WorkItemError for %s: %s",
                        item.reference,
                        error,
                    )
                    item.fail(str(error))

                except Exception:
                    logger.exception(
                        "Uventet fejl for item %s",
                        item.reference,
                    )

                    try:
                        if session.context and session.context.pages:
                            error_page = session.context.pages[-1]

                            await session.screenshot(
                                error_page,
                                "hard_exception",
                                always=True,
                            )
                    except Exception:
                        logger.warning(
                            "Kunne ikke tage screenshot ved hard error."
                        )

                    raise

    finally:
        await session.close()


# ------------------------------------------------------------
# MAIL-MODE
# ------------------------------------------------------------
def run_mail_mode() -> None:
    """Kør mailrobotten."""

    # Importeres først efter AutomationServer.from_environment().
    from mailrobot import run_mailrobot

    processed = run_mailrobot()
    print(f"Behandlede mails: {processed}")


# ------------------------------------------------------------
# MAIN ENTRY POINT
# ------------------------------------------------------------
def main() -> None:
    """Initialisér Automation Server og vælg kørselsform."""
    debug = "--debug" in sys.argv
    queue_mode = "--queue" in sys.argv
    mail_mode = "--mail" in sys.argv

    automation_server = AutomationServer.from_environment()

    if mail_mode:
        run_mail_mode()
        return

    workqueue = automation_server.workqueue()

    if queue_mode:
        asyncio.run(
            run_queue_mode(
                workqueue=workqueue,
                debug=debug,
            )
        )
        return

    asyncio.run(
        process_workqueue(
            workqueue=workqueue,
            debug=debug,
        )
    )


if __name__ == "__main__":
    main()