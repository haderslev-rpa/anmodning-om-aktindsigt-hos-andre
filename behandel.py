"""Procestrin og forretningslogik for ét work item."""
from datetime import date
import logging
import configuration as cfg
from document_service import create_docx_and_pdf, dansk_dato
from sharepoint_helpers import (
    add_list_attachment,
    download_list_attachment,
    get_template,
    list_item_attachments,
)
from signing_gateway import SigningGateway
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from q_haderslev_vbo.automation_server.ats_sharepoint import gem_sharepoint_list_item_til_box, hent_sharepoint_list_item_til_box

logger = logging.getLogger(__name__)

class States:
    BORGERBREV_GENERERET = "0.5 Brev til borger genereret"
    BORGERBREV_SENDT_TIL_SIGNERING = "1.0 Brev sendt til borger og afventer signering"
    SIGNERINGSPAAMINDELSE_SENDT = "1.2 Påmindelse om signering sendt"
    DIGITALT_SAMTYKKE_MODTAGET = "1.5 Digitalt samtykke modtaget"
    DIGITALT_SAMTYKKE_GEMT_SHAREPOINT = "1.6 Digitalt samtykke gemt i SharePoint"
    DIGITALT_SAMTYKKE_GEMT_FASIT = "1.7 Digitalt samtykke gemt i Fasit"
    MYNDIGHEDSBREV_GENERERET = "1.8 Brev til myndighed genereret"
    MYNDIGHEDSBREV_SENDT = "2.0 Brev sendt til virksomhed-myndighed"
    RYKKERBREV_GENERERET = "2.4 Rykkerbrev til myndighed genereret"
    RYKKERBREV_SENDT = "2.5 Rykkerbrev sendt til virksomhed-myndighed"
    SAGSAKT_GEMT_I_FASIT = "3.0 Sagsakt gemt i Fasit"
    AFSLUTTENDE_OPGAVE_OPRETTET = "4.0 Afsluttende opgave oprettet i Fasit"

    @classmethod
    def sagsakt_gemt_i_fasit(cls, file_name: str) -> str:
        return f"{cls.SAGSAKT_GEMT_I_FASIT}: {file_name}"

def mangler_state(data: dict, state: str) -> bool:
    return not any(state in existing for existing in data.get("state", []))

def set_state(data: dict, item, state: str) -> None:
    update_item_data(data, item=item, state=state)

def gem_sharepoint(item, updates: dict) -> None:
    item_id = item.data["box"]["sharepoint"][cfg.FIELD_ID]
    gem_sharepoint_list_item_til_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        sharepoint_data={cfg.FIELD_ID: item_id, **updates},
        item=item,
    )

def robotkommentar(item, text: str, **updates) -> None:
    gem_sharepoint(item, {**updates, "_robot_comment": text})

def template_values(sp: dict) -> dict[str, object]:
    return {"DATO": dansk_dato(date.today()), "SAGSNUMMER": sp[cfg.FIELD_CASE_NUMBER], "INSTITUTIONSNAVN": sp[cfg.FIELD_INSTITUTION]}

async def behandel_page(item, session, page):
    data = item.data
    data.setdefault("box", {})
    data["box"].setdefault("sharepoint", {})
    item_id = data["box"]["sharepoint"][cfg.FIELD_ID]
    hent_sharepoint_list_item_til_box(
        site_name=cfg.SHAREPOINT_SITE_NAME,
        list_name=cfg.SHAREPOINT_LIST_NAME,
        list_item_id=item_id,
        item=item,
    )
    sp = data["box"]["sharepoint"]
    sharepoint = SharePointGateway()
    signing = SigningGateway()

    if sp[cfg.FIELD_CONSENT] == cfg.VALUE_NO:
        if mangler_state(data, States.BORGERBREV_SENDT_TIL_SIGNERING):
            template = sharepoint.get_template(file_name=cfg.BORGERBREV_TEMPLATE_FILE_NAME)
            _, pdf = create_docx_and_pdf(
                template_bytes=template,
                values=template_values(sp),
                required_fields=cfg.BORGERBREV_PLACEHOLDERS,
                docx_file_name=cfg.BORGERBREV_DOCX_FILE_NAME,
                pdf_file_name=cfg.BORGERBREV_PDF_FILE_NAME,
            )
            data["box"]["signeringsportal"] = signing.create(cpr=sp[cfg.FIELD_CPR], citizen_letter=pdf)
            update_item_data(data, item=item)
            set_state(data, item, States.BORGERBREV_GENERERET)
            set_state(data, item, States.BORGERBREV_SENDT_TIL_SIGNERING)
            robotkommentar(item, "Brev er sendt til borger og afventer digital signering.")
            return

        flow_id = data["box"]["signeringsportal"]["flow_id"]
        status = signing.status(flow_id=flow_id)
        data["box"]["signeringsportal"]["status"] = status.status
        update_item_data(data, item=item)
        if status.afventer:
            return
        if status.er_afsluttet_uden_signatur:
            robotkommentar(item, f"Signeringsforløbet er afsluttet uden underskrift. Status: {status.status}.", **{cfg.FIELD_HANDLED_BY: cfg.VALUE_CASEWORKER})
            return
        if status.er_signeret:
            signed_pdf = signing.signed_pdf(flow_id=flow_id)
            set_state(data, item, States.DIGITALT_SAMTYKKE_MODTAGET)
            robotkommentar(item, 'Borger har underskrevet samtykket. Feltet "Er der givet samtykke?" er ændret til "Digitalt".', **{cfg.FIELD_CONSENT: cfg.VALUE_CONSENT_DIGITAL})
            sharepoint.add_list_attachment(item_id=int(item_id), document=signed_pdf)
            set_state(data, item, States.DIGITALT_SAMTYKKE_GEMT_SHAREPOINT)
            robotkommentar(item, f'Det underskrevne samtykke er gemt på SharePoint-itemet med navnet "{signed_pdf.file_name}".')
            data["box"]["documents_pending_fasit"] = [signed_pdf.file_name]
            update_item_data(data, item=item)
            return

    logger.info("Itemet er klar til næste procesdel eller afventer q-fasit-koblingen.")
