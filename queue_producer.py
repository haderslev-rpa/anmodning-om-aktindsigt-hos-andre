"""
Producer for processen Anmod om aktindsigt hos andre.

Producentens ansvar er:

1. Hent alle listeitems fra SharePoint-listen i configuration.py.
2. Vælg de listeitems, hvor "Behandles af" er sat til "Robot".
3. Byg ATS-referencen af:
   "{CPR} {Sagstype og dato}"
4. Kontrollér, om referencen allerede findes som:
   - NEW
   - PENDING_USER_ACTION
5. Opret kun et nyt work item, hvis referencen ikke findes.
6. Gem hele SharePoint-itemet i:
   item.data["box"]["sharepoint"]
"""

from __future__ import annotations

import logging
from typing import Any

import configuration as cfg

from q_haderslev_vbo.automation_server.ats_is_item_in_queue import (
    is_item_in_queue,
)
from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_sharepoint_api.functionality.sp_list_items import (
    get_list_items,
)


logger = logging.getLogger(__name__)


def _get_workqueue_id(workqueue) -> int:
    """
    Henter det tekniske queue-id fra Automation Server-workqueueobjektet.

    Returnerer:
        Køens tekniske id som et positivt heltal.
    """
    queue_id = getattr(workqueue, "id", None)

    if queue_id is None:
        raise RuntimeError(
            "Automation Server returnerede en workqueue uden id. "
            "Kontrollér ATS_WORKQUEUE_OVERRIDE og processens workqueue."
        )

    try:
        validated_queue_id = int(queue_id)
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            f"Workqueue-id er ugyldigt: {queue_id!r}."
        ) from error

    if validated_queue_id <= 0:
        raise RuntimeError(
            "Workqueue-id skal være større end 0. "
            f"Modtaget værdi: {validated_queue_id}."
        )

    return validated_queue_id


def _get_sharepoint_items() -> list[dict[str, Any]]:
    """
    Henter alle SharePoint-items fra listen i configuration.py.

    Returnerer:
        En liste med SharePoint-items som dictionaries.
    """
    result = get_list_items(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
    )

    if not isinstance(result, dict):
        raise TypeError(
            "get_list_items() returnerede ikke en dictionary."
        )

    items = result.get("items")

    if items is None:
        return []

    if not isinstance(items, list):
        raise TypeError(
            "SharePoint-resultatets 'items' er ikke en liste."
        )

    for item in items:
        if not isinstance(item, dict):
            raise TypeError(
                "SharePoint-resultatet indeholder et item, "
                "som ikke er en dictionary."
            )

    return items


def _get_required_text(
    sharepoint_item: dict[str, Any],
    field_name: str,
) -> str:
    """
    Henter et obligatorisk tekstfelt fra et SharePoint-item.

    Returnerer:
        Feltets værdi som trimmet tekst.

    Rejser:
        ValueError, hvis feltet mangler eller er tomt.
    """
    value = sharepoint_item.get(field_name)

    if value is None:
        raise ValueError(
            "SharePoint-itemet mangler det obligatoriske felt "
            f"{field_name!r}."
        )

    validated_value = str(value).strip()

    if not validated_value:
        raise ValueError(
            "SharePoint-itemet har en tom værdi i feltet "
            f"{field_name!r}."
        )

    return validated_value


def _get_sharepoint_item_id(
    sharepoint_item: dict[str, Any],
) -> str:
    """
    Henter SharePoint-itemets id.

    Returnerer:
        SharePoint-id som tekst.
    """
    return _get_required_text(
        sharepoint_item=sharepoint_item,
        field_name=cfg.FIELD_ID,
    )


def _is_ready_for_robot(
    sharepoint_item: dict[str, Any],
) -> bool:
    """
    Returnerer True, når SharePoint-itemets "Behandles af"
    er sat til "Robot".
    """
    handled_by = sharepoint_item.get(
        cfg.FIELD_HANDLED_BY
    )

    if handled_by is None:
        return False

    return (
        str(handled_by).strip().casefold()
        == str(cfg.VALUE_ROBOT).strip().casefold()
    )


def _build_reference(
    sharepoint_item: dict[str, Any],
) -> str:
    """
    Bygger ATS-referencen af CPR og Sagstype og dato.

    Eksempel:
        CPR:
            0101852289

        Sagstype og dato:
            Flexjobsag - 01-01-2026

        Returneret reference:
            0101852289 Flexjobsag - 01-01-2026
    """
    cpr = _get_required_text(
        sharepoint_item=sharepoint_item,
        field_name=cfg.FIELD_CPR,
    )

    case_type_and_date = _get_required_text(
        sharepoint_item=sharepoint_item,
        field_name=cfg.FIELD_CASE_TYPE_AND_DATE,
    )

    return f"{cpr} {case_type_and_date}"


def _build_item_data(
    sharepoint_item: dict[str, Any],
) -> dict[str, Any]:
    """
    Bygger den komplette datastruktur til Automation Server.

    Returnerer:
        {
            "box": {
                "sharepoint": {
                    ...alle SharePoint-felter...
                }
            }
        }
    """
    data: dict[str, Any] = {}

    update_item_data(
        data,
        box_updates={
            "sharepoint": dict(sharepoint_item),
        },
        update=False,
    )

    box = data.get("box")

    if not isinstance(box, dict):
        raise RuntimeError(
            "update_item_data() oprettede ikke "
            "data['box'] korrekt."
        )

    sharepoint_box = box.get("sharepoint")

    if not isinstance(sharepoint_box, dict):
        raise RuntimeError(
            "SharePoint-data blev ikke placeret korrekt i "
            "data['box']['sharepoint']."
        )

    return data


def _reference_already_waits_in_queue(
    *,
    queue_id: int,
    reference: str,
) -> bool:
    """
    Kontrollerer, om referencen allerede findes i ATS-køen.

    Følgende statusser kontrolleres:
        - NEW
        - PENDING_USER_ACTION

    Returnerer:
        True, hvis referencen allerede findes i en af statusserne.
        False, hvis referencen ikke findes.
    """
    return is_item_in_queue(
        queue_id=queue_id,
        item_reference=reference,
        new=True,
        in_progress=False,
        completed=False,
        failed=False,
        pending_user_action=True,
    )


async def populate_queue(
    workqueue,
    debug: bool,
) -> None:
    """
    Henter SharePoint-items og tilføjer manglende items til ATS-køen.

    Kun SharePoint-items, hvor "Behandles af" er "Robot",
    bliver vurderet til køen.
    """
    queue_id = _get_workqueue_id(workqueue)
    sharepoint_items = _get_sharepoint_items()

    logger.info(
        "Producer startet. "
        "queue_id=%s, SharePoint-items=%s, debug=%s",
        queue_id,
        len(sharepoint_items),
        debug,
    )

    created_count = 0
    duplicate_count = 0
    ignored_count = 0

    for sharepoint_item in sharepoint_items:
        if not _is_ready_for_robot(sharepoint_item):
            ignored_count += 1
            continue

        sharepoint_item_id = _get_sharepoint_item_id(
            sharepoint_item
        )

        reference = _build_reference(
            sharepoint_item
        )

        if _reference_already_waits_in_queue(
            queue_id=queue_id,
            reference=reference,
        ):
            duplicate_count += 1

            logger.info(
                "Springer SharePoint-item %s over. "
                "Referencen %r findes allerede som "
                "NEW eller PENDING_USER_ACTION.",
                sharepoint_item_id,
                reference,
            )

            continue

        item_data = _build_item_data(
            sharepoint_item
        )

        workqueue.add_item(
            data=item_data,
            reference=reference,
        )

        created_count += 1

        logger.info(
            "Oprettede ATS-item. "
            "SharePoint-id=%s, reference=%r",
            sharepoint_item_id,
            reference,
        )

    logger.info(
        "Producer afsluttet. "
        "Oprettet=%s, dubletter=%s, ignoreret=%s.",
        created_count,
        duplicate_count,
        ignored_count,
    )