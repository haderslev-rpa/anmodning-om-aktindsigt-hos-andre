"""
Konfiguration for processen Anmod om aktindsigt hos andre.

Filen indeholder kun værdier, som kan ændres uden at ændre
proceslogikken.

Secrets og interne serviceadresser ligger i Automation Server.
"""


# ------------------------------------------------------------
# SHAREPOINT
# ------------------------------------------------------------

SHAREPOINT_SITE_NAME = "Automatisering"

SHAREPOINT_LIST_NAME = (
    "Anmodning om aktindsigt hos andre"
)

SHAREPOINT_TEMPLATE_FOLDER = (
    "RPA - Processer/"
    "anmodning-om-aktindsigt-hos-andre/"
    "Skabeloner"
)

BORGERBREV_TEMPLATE_FILE_NAME = (
    "Samtykke_til_borger.docx"
)

MYNDIGHEDSBREV_TEMPLATE_FILE_NAME = (
    "Anmodning_til_myndighed.docx"
)

RYKKERBREV_TEMPLATE_FILE_NAME = (
    "Rykker_til_myndighed.docx"
)


# ------------------------------------------------------------
# GENEREREDE DOKUMENTER
# ------------------------------------------------------------

BORGERBREV_DOCX_FILE_NAME = (
    "Samtykke til borger.docx"
)

BORGERBREV_PDF_FILE_NAME = (
    "Samtykke til borger.pdf"
)

MYNDIGHEDSBREV_DOCX_FILE_NAME = (
    "Anmodning om aktindsigt.docx"
)

MYNDIGHEDSBREV_PDF_FILE_NAME = (
    "Anmodning om aktindsigt.pdf"
)

RYKKERBREV_DOCX_FILE_NAME = (
    "Rykker om aktindsigt.docx"
)

RYKKERBREV_PDF_FILE_NAME = (
    "Rykker om aktindsigt.pdf"
)


# ------------------------------------------------------------
# SHAREPOINT-VEDHÆFTNINGER
# ------------------------------------------------------------

UNSIGNED_CONSENT_FILE_NAME = (
    "Sendt til signering hos borger - ikke signeret.pdf"
)

SIGNED_CONSENT_FILE_NAME = (
    "Samtykke fra borger - signeret.pdf"
)

AUTHORITY_CONSENT_ATTACHMENT_FILE_NAME = (
    "Samtykke fra borger.pdf"
)

ARCHIVED_AUTHORITY_LETTER_FILE_NAME = (
    "Afsendt anmodning om aktindsigt.pdf"
)

REGENERATED_AUTHORITY_LETTER_FILE_NAME = (
    "Regenereret anmodning om aktindsigt.pdf"
)

ARCHIVED_REMINDER_FILE_NAME = (
    "Afsendt rykker om aktindsigt.pdf"
)

REGENERATED_REMINDER_FILE_NAME = (
    "Regenereret rykker om aktindsigt.pdf"
)

CONSENT_IMAGE_EXTENSIONS = (
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
)

CASE_FILE_EXTENSIONS = (
    ".eml",
)


# ------------------------------------------------------------
# WORD-PLADSHOLDERE
# ------------------------------------------------------------

BORGERBREV_PLACEHOLDERS = (
    "NAVN",
    "ADRESSE",
    "POSTNUMMER_BY",
    "CPR",
    "DATO",
    "SAGSTYPE_OG_DATO",
    "INSTITUTIONSNAVN",
)

MYNDIGHEDSBREV_PLACEHOLDERS = (
    "DATO",
    "SAGSTYPE_OG_DATO",
    "INSTITUTIONSNAVN",
)

RYKKERBREV_PLACEHOLDERS = (
    "DATO",
    "SAGSTYPE_OG_DATO",
    "INSTITUTIONSNAVN",
)


# ------------------------------------------------------------
# SIGNERINGSPORTALEN
# ------------------------------------------------------------

SIGNING_WORKFLOW_NAME = (
    "Samtykke til indhentning af sagsakter"
)

SIGNING_RECIPIENT_TYPE = "DigitalPost"

SIGNING_MESSAGE = (
    "Dokumentet skal underskrives."
)

SIGNING_EXPIRY_DAYS = 14


# ------------------------------------------------------------
# FRISTER
# ------------------------------------------------------------

AUTHORITY_RESPONSE_DAYS = 7
AUTHORITY_REMINDER_RESPONSE_DAYS = 7


# ------------------------------------------------------------
# FASIT
# ------------------------------------------------------------

FASIT_ENABLED = False

FASIT_CREDENTIAL_NAME = "DIRXOPS"
FASIT_MYNDIGHEDSBREV_TYPE = "INDSAET_BREVTYPE"
FASIT_RYKKERBREV_TYPE = "INDSAET_BREVTYPE"
FASIT_DOCUMENT_CATEGORY = "INDSAET_DOKUMENTKATEGORI"
FASIT_TASK_CATEGORY_ID = "INDSAET_UUID"
FASIT_TASK_CASE_TYPE = "INDSAET_SAGSTYPE"
FASIT_TASK_STATUS = "Planlagt"
FASIT_TASK_TITLE = "Sagsakter modtaget"

FASIT_TASK_DESCRIPTION = (
    "Robotten har indhentet og journaliseret sagsakter. "
    "Sagen er klar til sagsbehandlerens videre behandling."
)

FASIT_TASK_DUE_DAYS = 0


# ------------------------------------------------------------
# SHAREPOINT-FELTNAVNE
# ------------------------------------------------------------

FIELD_ID = "id"
FIELD_CPR = "CPR"
FIELD_CASE_NUMBER = "Sagsnr"
FIELD_P_NUMBER = "P-nummer"
FIELD_INSTITUTION = "Institutionsnavn"
FIELD_CONSENT = "Er der givet samtykke?"

FIELD_LETTER_STATUS = (
    "Brevstatus til virksomhed-myndighed"
)

FIELD_CASE_FILE_RECEIVED = (
    "Modtaget sagsakter?"
)

FIELD_ROBOT_COMMENT = "Robot kommentar"
FIELD_HANDLED_BY = "Behandles af"
FIELD_DELETE_ROW = "Slet linjen?"
FIELD_CASE_TYPE_AND_DATE = "Sagstype og dato"


# ------------------------------------------------------------
# SHAREPOINT-VÆRDIER
# ------------------------------------------------------------

VALUE_YES = "Ja"
VALUE_NO = "Nej"
VALUE_ROBOT = "Robot"
VALUE_CASEWORKER = "Sagsbehandler"

VALUE_CONSENT_ORAL_WRITTEN = (
    "Mundtligt/skriftligt"
)

VALUE_CONSENT_DIGITAL = "Digitalt"
VALUE_LETTER_NOT_SENT = "Ikke sendt"
VALUE_LETTER_SENT = "Sendt brev"
VALUE_REMINDER_SENT = "Sendt rykkerbrev"
