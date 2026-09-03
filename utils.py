from typing import Optional, TYPE_CHECKING, Iterable
from xml.sax.saxutils import escape
import zipfile

if TYPE_CHECKING:
    from config import Config


def safe_username(username: Optional[str], user_id: Optional[int] = None) -> str:
    if username:
        return f"@{username}" if not username.startswith("@") else username
    return f"id{user_id}" if user_id else "Пользователь"


def work_chat_user(username: Optional[str], user_id: int) -> str:
    """Format a user tag together with their ID for internal work-chat messages."""
    return f"{safe_username(username, user_id)} (id {user_id})"


def _internal_chat_id(chat_id: int) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s


def build_request_link(cfg: "Config", request: dict) -> Optional[str]:
    msg_id = request.get("channel_message_id")
    if not msg_id:
        return None
    internal = _internal_chat_id(cfg.REQUESTS_PUBLIC_CHANNEL_ID)
    return f"https://t.me/c/{internal}/{msg_id}"


def build_direct_link(cfg: "Config", message_id: int) -> str:
    internal = _internal_chat_id(cfg.REQUESTS_PUBLIC_CHANNEL_ID)
    return f"https://t.me/c/{internal}/{message_id}"


def write_simple_xlsx(path: str, sheet_name: str, rows: Iterable[Iterable[str]]) -> None:
    sheet_xml_rows = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            col_letter = chr(ord("A") + col_idx - 1)
            cell_ref = f"{col_letter}{row_idx}"
            cell_text = escape(str(value))
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>{cell_text}</t></is></c>'
            )
        sheet_xml_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    sheet_data = "".join(sheet_xml_rows)
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{sheet_data}</sheetData>"
        "</worksheet>"
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        f'<sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
        "</sheets>"
        "</workbook>"
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
