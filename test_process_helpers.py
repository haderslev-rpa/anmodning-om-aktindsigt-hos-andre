from datetime import date

import configuration as cfg
from behandel import template_values


def test_template_values_extracts_case_type_and_today(monkeypatch):
    sharepoint_data = {
        cfg.FIELD_CASE_TYPE_AND_DATE: "Fleksjob - 01-01-2027",
        cfg.FIELD_CITIZEN_NAME: "Test Testesen",
        cfg.FIELD_CPR: "0101010101",
        cfg.FIELD_INSTITUTION: "Testmyndighed",
    }

    values = template_values(sharepoint_data)

    assert values["SAGSTYPE"] == "Fleksjob"
    assert values["DATO"]
    assert values["FLEKSJOB"] == cfg.FLEXJOB_AUTHORITY_TEXT


def test_template_values_blanks_flexjob_for_other_case_type():
    sharepoint_data = {
        cfg.FIELD_CASE_TYPE_AND_DATE: "Sygedagpenge - 01-01-2027",
        cfg.FIELD_CITIZEN_NAME: "Test Testesen",
        cfg.FIELD_CPR: "0101010101",
        cfg.FIELD_INSTITUTION: "Testmyndighed",
    }

    values = template_values(sharepoint_data)

    assert values["SAGSTYPE"] == "Sygedagpenge"
    assert values["FLEKSJOB"] == ""

