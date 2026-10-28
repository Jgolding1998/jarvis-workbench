from __future__ import annotations

import ctypes
import os
import sys
import zipfile
from pathlib import Path


def create_boss_sheet(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Boss Alert" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>BOSS ALERT: DEMONSTRATION DATA</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Item</t></is></c><c r="B2" t="inlineStr"><is><t>Status</t></is></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>Local camera detected an additional person</t></is></c><c r="B3" t="inlineStr"><is><t>Review surroundings</t></is></c></row></sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


class Actions:
    def __init__(self, demo=True, allow_lock=False, allow_sheet=False, output_dir=None):
        self.demo = demo
        self.allow_lock = allow_lock
        self.allow_sheet = allow_sheet
        self.output_dir = Path(output_dir or "runtime")

    def run(self, action):
        if action not in {"lock_absence", "lock_camera_fault", "lock_crowd", "boss_alert"}:
            raise ValueError("Action is not allowlisted")
        if self.demo:
            return "Simulated " + action
        if sys.platform != "win32":
            raise RuntimeError("Desktop actions require Windows")
        if action.startswith("lock_"):
            if not self.allow_lock:
                raise PermissionError("Lock action disabled at launch")
            if not ctypes.windll.user32.LockWorkStation():
                raise OSError("Windows rejected the lock request")
            return "Requested Windows lock"
        if not self.allow_sheet:
            raise PermissionError("Spreadsheet action disabled at launch")
        path = create_boss_sheet(self.output_dir / "Boss Alert.xlsx").resolve()
        os.startfile(path)
        return "Opened synthetic Boss Alert workbook"
