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
    """
    Læs HEADLESS fra .env.

    HEADLESS=true:
        Browseren vises ikke.

    HEADLESS=false:
        Browseren vises under lokal debugging.
    """
    return os.getenv(
        "HEADLESS",
        "true",
    ).strip().lower() == "true"


def _vaelg_items_til_behandling(
    workqueue: Workqueue,
):
    """
    Vælg hele køen eller ét bestemt NEW-item ved lokal debugging.

    Hvis DEBUG_ITEM_REFERENCE er tom:
        Returneres workqueue, og Automation Server leverer næste item.

    Hvis DEBUG_ITEM_REFERENCE har en værdi:
        Findes det første NEW-item med den reference.
    """
    reference = os.getenv(
        "DEBUG_ITEM_REFERENCE",
        "",
    ).strip()

    if not reference:
        return workqueue

    items = workqueue.get_item_by_reference(
        reference=reference,
        status=WorkItemStatus.NEW,
    )

    if not items:
        raise RuntimeError(
            "Ingen NEW-items fundet med reference: "
            f"{reference}"
        )

    item = items[0]

    item.update_status(
        WorkItemStatus.IN_PROGRESS.value,
        "Startet via lokal debugkørsel",
    )

    return [item]


# ------------------------------------------------------------
# QUEUE-MODE
# ------------------------------------------------------------

async def run_queue_mode(
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """
    Kør queue_producer.py.

    Producenten henter SharePoint-items og opretter de items,
    som ikke allerede findes i køen.
    """
    # Importen ligger her, så producerens integrationer først
    # indlæses efter AutomationServer.from_environment().
    from queue_producer import populate_queue

    logger.info(
        "Queue mode startet. debug=%s",
        debug,
    )

    await populate_queue(
        workqueue=workqueue,
        debug=debug,
    )


# ------------------------------------------------------------
# WORKER-MODE
# ------------------------------------------------------------

async def process_workqueue(
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """
    Behandl work items ét ad gangen.

    main.py ejer:
        - Playwright-sessionen
        - browserens livscyklus
        - overordnet fejlhåndtering
        - indlæsning af næste item

    behandel.py ejer:
        - forretningslogik
        - states
        - SharePoint-data
        - dokumentgenerering
        - Signeringsportalen
    """
    # Importeres først efter AutomationServer.from_environment().
    from behandel import behandel_page

    logger.info(
        "Worker mode startet. debug=%s",
        debug,
    )

    session = BrowserSession(
        headless=get_headless_flag(),
        debug=debug,
    )

    await session.start()
    page = await session.new_page()

    try:
        for item in _vaelg_items_til_behandling(
            workqueue
        ):
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

                    # behandel.py kan have ændret itemets status til
                    # PENDING_USER_ACTION. Derfor completer main.py
                    # ikke itemet ubetinget.
                    current_status = getattr(
                        item,
                        "status",
                        None,
                    )

                    if current_status in (
                        WorkItemStatus.PENDING_USER_ACTION,
                        WorkItemStatus.PENDING_USER_ACTION.value,
                    ):
                        logger.info(
                            "Item %s afventer ekstern handling.",
                            item.reference,
                        )
                        continue

                    item.update(item.data)
                    item.complete("Completed")

                    logger.info(
                        "Item %s er completed.",
                        item.reference,
                    )

                except WorkItemError as error:
                    logger.error(
                        "WorkItemError for %s: %s",
                        item.reference,
                        error,
                    )

                    item.fail(str(error))

                except Exception as error:
                    logger.exception(
                        "Uventet fejl for item %s",
                        item.reference,
                    )

                    try:
                        if (
                            session.context
                            and session.context.pages
                        ):
                            error_page = (
                                session.context.pages[-1]
                            )

                            await session.screenshot(
                                error_page,
                                (
                                    "hard_exception_"
                                    f"{type(error).__name__}"
                                ),
                                always=True,
                            )

                    except Exception:
                        logger.warning(
                            "Kunne ikke tage screenshot "
                            "ved hard error."
                        )

                    # Tekniske fejl løftes videre, så Automation
                    # Server kan håndtere processens genstart.
                    raise

    finally:
        await session.close()


# ------------------------------------------------------------
# MAIN ENTRY POINT
# ------------------------------------------------------------

def main() -> None:
    """
    Initialisér Automation Server og vælg kørselsform.

    --queue:
        Kør queue_producer.py.

    --debug:
        Aktivér lokal debugging.

    Uden --queue:
        Tag næste item og kald behandel.py.
    """
    debug = "--debug" in sys.argv
    queue_mode = "--queue" in sys.argv

    automation_server = (
        AutomationServer.from_environment()
    )

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