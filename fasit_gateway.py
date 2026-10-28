"""Fasit-opslag og validering af borgerens sag."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Iterator

import configuration as cfg
from models import BinaryFile
from q_fasit.api.borger import SAGER, hent_borger_id
from q_fasit.api.client import FasitApiClient
from q_fasit.api.token_manager import FasitTokenManager


@dataclass(frozen=True)
class FasitCaseMatch:
    """Den Fasit-sag, processen har valideret."""

    citizen_id: str
    case_id: str
    case_type: str
    case_date: date
    status: str
    raw_case: dict[str, Any]


class FasitCaseValidationError(RuntimeError):
    """Forretningsfejl, der skal sendes tilbage til sagsbehandleren."""


class FasitGateway:
    """Ejer FasitTokenManager og FasitApiClient for ét worker-forløb."""

    def __init__(self) -> None:
        self._token_manager = FasitTokenManager(
            credential_name=cfg.FASIT_CREDENTIAL_NAME,
            headless=cfg.FASIT_HEADLESS,
            fallback_lifetime_seconds=15 * 60,
            expiry_margin_seconds=60,
        )
        self._api_client = FasitApiClient(
            token_manager=self._token_manager,
        )

    async def close(self) -> None:
        """Luk API-session og token-managerens browser-session."""
        await self._api_client.close()
        await self._token_manager.close()

    async def validate_citizen_case(
        self,
        *,
        cpr: str,
        expected_case_type: str,
        expected_case_date: date,
    ) -> FasitCaseMatch:
        """Find borgeren og kontrollér matchende, åben Fasit-sag.

        Valideringen er altid et rigtigt Fasit-opslag. FASIT_SIMULATION
        påvirker kun skrivehandlinger, ikke denne indledende kontrol.
        """
        try:
            citizen_id = await hent_borger_id(
                api_client=self._api_client,
                cpr=cpr,
                opret_borger_hvis_ikke_findes=False,
            )
        except RuntimeError as error:
            raise FasitCaseValidationError(
                "Borgeren kunne ikke findes i Fasit. "
                "Kontrollér CPR-nummeret."
            ) from error

        result = await SAGER(
            api_client=self._api_client,
            citizen_id=citizen_id,
        )
        cases = _extract_case_dictionaries(result)
        if not cases:
            raise FasitCaseValidationError(
                "Borgeren findes i Fasit, men Fasit returnerede ingen sager."
            )

        case_type_matches = [
            case
            for case in cases
            if _case_type_matches(case, expected_case_type)
        ]
        if not case_type_matches:
            raise FasitCaseValidationError(
                "Der blev ikke fundet en Fasit-sag, der matcher "
                f"sagstypen '{expected_case_type}'."
            )

        date_matches = [
            case
            for case in case_type_matches
            if _case_date_matches(case, expected_case_date)
        ]
        if not date_matches:
            raise FasitCaseValidationError(
                "Sagstypen blev fundet i Fasit, men ingen sag matchede "
                f"datoen {expected_case_date.strftime('%d-%m-%Y')}."
            )

        active_matches = [
            case
            for case in date_matches
            if not _is_closed_case(case)
        ]
        if not active_matches:
            raise FasitCaseValidationError(
                "Sagen blev fundet i Fasit, men sagen er lukket eller "
                "afsluttet."
            )

        if len(active_matches) > 1:
            raise FasitCaseValidationError(
                "Der blev fundet flere åbne Fasit-sager med samme "
                "sagstype og dato. Sagsbehandler skal afklare den "
                "korrekte sag."
            )

        selected_case = active_matches[0]
        case_id = _first_text(selected_case, cfg.FASIT_CASE_ID_KEYS)
        if not case_id:
            raise RuntimeError(
                "Den matchende Fasit-sag manglede et sags-id."
            )

        return FasitCaseMatch(
            citizen_id=citizen_id,
            case_id=case_id,
            case_type=expected_case_type,
            case_date=expected_case_date,
            status=(
                _first_text(selected_case, cfg.FASIT_CASE_STATUS_KEYS)
                or "Ukendt"
            ),
            raw_case=selected_case,
        )

    async def save_document(
        self,
        *,
        case_match: FasitCaseMatch,
        document: BinaryFile,
        title: str,
    ) -> dict[str, Any]:
        """Gem et dokument eller simulér skrivehandlingen."""
        if cfg.FASIT_SIMULATION:
            return self._simulate(
                "save_document",
                {
                    "case_id": case_match.case_id,
                    "file_name": document.file_name,
                    "title": title,
                    "size_bytes": len(document.content),
                },
            )
        raise NotImplementedError(
            "Kobl kollegaens nye q-fasit-funktion til save_document()."
        )

    async def create_task(
        self,
        *,
        case_match: FasitCaseMatch,
        title: str,
        description: str,
    ) -> dict[str, Any]:
        """Opret en opgave eller simulér skrivehandlingen."""
        if cfg.FASIT_SIMULATION:
            return self._simulate(
                "create_task",
                {
                    "case_id": case_match.case_id,
                    "title": title,
                    "description": description,
                },
            )
        raise NotImplementedError(
            "Kobl q-fasits opgavefunktion til create_task()."
        )

    @staticmethod
    def _simulate(
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = {
            "simulation": True,
            "action": action,
            **payload,
        }
        with Path(cfg.FASIT_SIMULATION_LOG_FILE).open(
            "a",
            encoding="utf-8",
        ) as simulation_file:
            simulation_file.write(
                json.dumps(result, ensure_ascii=False) + "\n"
            )
        return result


def _walk(value: Any) -> Iterator[Any]:
    """Gennemløb Fasit-responsen rekursivt."""
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _extract_case_dictionaries(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Find dictionaries, der ligner Fasit-sager."""
    cases: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for value in _walk(result):
        if not isinstance(value, dict):
            continue
        has_case_type = _first_text(value, cfg.FASIT_CASE_TYPE_KEYS) is not None
        has_case_id = _first_text(value, cfg.FASIT_CASE_ID_KEYS) is not None
        has_case_date = _first_value(value, cfg.FASIT_CASE_START_DATE_KEYS) is not None
        if not (has_case_type and has_case_id and has_case_date):
            continue
        identity = id(value)
        if identity not in seen_ids:
            seen_ids.add(identity)
            cases.append(value)

    return cases


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _first_value(
    source: dict[str, Any],
    keys: tuple[str, ...],
) -> Any | None:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_text(
    source: dict[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    value = _first_value(source, keys)
    if value in (None, ""):
        return None
    return str(value).strip()


def _case_type_matches(
    case: dict[str, Any],
    expected_case_type: str,
) -> bool:
    actual_case_type = _first_text(case, cfg.FASIT_CASE_TYPE_KEYS)
    if not actual_case_type:
        return False
    return _normalize_text(actual_case_type) == _normalize_text(expected_case_type)


def _parse_fasit_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    normalized_value = value.strip().replace("Z", "+00:00")
    if not normalized_value:
        return None

    try:
        return datetime.fromisoformat(normalized_value).date()
    except ValueError:
        pass

    for date_format in (
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(
                normalized_value,
                date_format,
            ).date()
        except ValueError:
            continue

    return None


def _case_date_matches(
    case: dict[str, Any],
    expected_case_date: date,
) -> bool:
    actual_case_date = _parse_fasit_date(
        _first_value(case, cfg.FASIT_CASE_START_DATE_KEYS)
    )
    return actual_case_date == expected_case_date


def _is_closed_case(case: dict[str, Any]) -> bool:
    end_date = _parse_fasit_date(
        _first_value(case, cfg.FASIT_CASE_END_DATE_KEYS)
    )
    if end_date is not None:
        return True

    status = _normalize_text(
        _first_text(case, cfg.FASIT_CASE_STATUS_KEYS)
    )
    return any(
        closed_status.casefold() in status
        for closed_status in cfg.FASIT_CLOSED_CASE_STATUSES
    )
