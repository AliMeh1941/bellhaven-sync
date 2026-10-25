"""Settings. Field names marked CONFIRM get checked against the real API spec."""
import os

BASE_URL = os.getenv("BH_BASE_URL", "https://analyst-assessment-production.up.railway.app")
API_URL = BASE_URL + "/api/v1"
TOKEN = os.getenv("BH_TOKEN", "")
DB_PATH = os.getenv("BH_DB", "state/bellhaven_sync.db")

# Optional: pin the canonical Bellhaven parent account id once we know it.
BELLHAVEN_PARENT_ID = os.getenv("BH_PARENT_ID")
CORPORATE_STREET = "400 Bellhaven Way"

# HTTP verb the API uses to update one account (confirmed: PATCH)
UPDATE_METHOD = "PATCH"
PAGE_SIZE = 100

# Account field names (confirmed from the API). First name found on the record
# wins; the first name in each list is what new records are written with.
ID_FIELD = "account_id"
ALIASES = {
    "name": ["name"],
    "street": ["billing_street", "address"],
    "city": ["billing_city", "city"],
    "state": ["billing_state", "state"],
    "zip": ["billing_zip", "zip"],
    "parent_id": ["parent_id"],
    "status": ["status"],
    "note": ["note"],
    "care": ["care_type", "care_offerings"],
    "lifetime_revenue": ["lifetime_revenue"],
    "outstanding_ar": ["outstanding_ar"],
    "duplicate_of_account": ["duplicate_of_account"],
    "chow_current_account": ["chow_current_account"],
}

# From Bellhaven's About page. Year only; the site gives no exact dates.
ACQUISITIONS = {"harborview": "2025", "cedar trail": "2026"}

ACTIVE, INACTIVE, NEEDS_REVIEW = "Active", "Inactive", "Needs Review"

# Matching thresholds (0 to 100)
STREET_MATCH = 88   # street similarity for an address match
NAME_MATCH = 90     # name similarity when the CRM record has no street
POSSIBLE_NAME = 80  # shown to the reviewer as "possible" only
