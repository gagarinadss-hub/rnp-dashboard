import gspread
from google.oauth2.service_account import Credentials
import json
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1bDbcketkM9SC5FY0rMHvwy8bUaf4Op3hPxFQJHBKH9E")


def _build_creds() -> Credentials:
    json_env = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if json_env:
        info = json.loads(json_env)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    path = os.getenv("CREDENTIALS_PATH") or os.path.join(os.path.dirname(__file__), "credentials.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Файл {path} не найден. Настрой Google API по инструкции в README.md"
        )
    return Credentials.from_service_account_file(path, scopes=SCOPES)


class SheetsClient:
    def __init__(self):
        creds = _build_creds()
        gc = gspread.authorize(creds)
        self.spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    def _find_ws(self, keywords: list) -> gspread.Worksheet:
        for ws in self.spreadsheet.worksheets():
            title = ws.title.lower()
            if any(k.lower() in title for k in keywords):
                return ws
        titles = [ws.title for ws in self.spreadsheet.worksheets()]
        raise ValueError(f"Лист не найден ({keywords}). Доступны: {titles}")

    def get_registrations(self) -> list[dict]:
        ws = self._find_ws(["база", "база"])
        return ws.get_all_records()

    def get_rnp_raw(self) -> list[list]:
        # Sheet named "РНП ✅" — pick first РНП sheet, skip backup/old copies
        for ws in self.spreadsheet.worksheets():
            t = ws.title
            if t.startswith("РНП") and "стар" not in t.lower() and "копи" not in t.lower():
                return ws.get_all_values()
        raise ValueError("Лист РНП не найден")

    def get_historical_raw(self) -> list[list]:
        try:
            ws = self._find_ws(["процент", "для расчёта", "дни"])
            return ws.get_all_values()
        except ValueError:
            return []
