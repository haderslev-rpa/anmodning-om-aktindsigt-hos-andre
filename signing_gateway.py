from datetime import datetime, timedelta, timezone
import configuration as cfg
from models import BinaryFile
from q_signeringsportalen_api import SigneringsportalClient

class SigningGateway:
    def create(self, *, cpr: str, letter: BinaryFile) -> dict:
        normalized_cpr = "".join(character for character in cpr if character.isdigit())
        with SigneringsportalClient.from_automation_server() as client:
            if not client.kan_modtage_digital_post_cpr(normalized_cpr):
                raise RuntimeError("Borgeren kan ikke modtage Digital Post.")
            result = client.opret_signeringsopgave(
                navn=cfg.SIGNING_WORKFLOW_NAME,
                pdf_filer=[{"titel": letter.file_name, "indhold": letter.content}],
                underskrivere=[{
                    "identifikator": normalized_cpr,
                    "type": cfg.SIGNING_RECIPIENT_TYPE,
                    "besked": cfg.SIGNING_MESSAGE,
                }],
                udloeber=datetime.now(timezone.utc) + timedelta(days=cfg.SIGNING_EXPIRY_DAYS),
                should_creator_sign=False,
                is_structured=False,
            )
        return {"flow_id": result.flow_id, "status": "Pending", "created_at": datetime.now(timezone.utc).isoformat()}

    def status(self, flow_id: str):
        with SigneringsportalClient.from_automation_server() as client:
            return client.hent_signeringsstatus(flow_id)

    def signed_pdf(self, flow_id: str) -> BinaryFile:
        with SigneringsportalClient.from_automation_server() as client:
            content = client.hent_signeret_pdf(flow_id)
        return BinaryFile(cfg.DIGITAL_CONSENT_FILE_NAME, content, "application/pdf")

    def remind(self, flow_id: str, signer_id: str) -> None:
        with SigneringsportalClient.from_automation_server() as client:
            client.send_paamindelse(flow_id=flow_id, signer_id=signer_id)

    def delete(self, flow_id: str) -> None:
        with SigneringsportalClient.from_automation_server() as client:
            client.slet_signeringsopgave(flow_id)
