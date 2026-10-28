from datetime import date

import configuration as cfg
from fasit_gateway import (
    _case_date_matches,
    _case_type_matches,
    _is_closed_case,
)


def test_case_type_and_date_match():
    case = {
        "caseId": "11111111-1111-1111-1111-111111111111",
        "caseType": "Fleksjob",
        "caseStartDate": "2027-01-01T00:00:00Z",
        "caseStatus": "Aktiv",
    }

    assert _case_type_matches(case, "Fleksjob") is True
    assert _case_date_matches(case, date(2027, 1, 1)) is True
    assert _is_closed_case(case) is False


def test_case_with_end_date_is_closed():
    case = {
        "caseId": "11111111-1111-1111-1111-111111111111",
        "caseType": "Fleksjob",
        "caseStartDate": "2027-01-01",
        "caseEndDate": "2027-02-01",
        "caseStatus": "Aktiv",
    }

    assert _is_closed_case(case) is True


def test_closed_status_is_closed():
    case = {
        "caseStatus": cfg.FASIT_CLOSED_CASE_STATUSES[0],
    }

    assert _is_closed_case(case) is True
