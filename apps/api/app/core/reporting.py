"""Filtered management report snapshots and dependency-free CSV/XLSX exports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from html import escape
from pathlib import Path

DISCLAIMER = "Decision Support: рекомендации не являются обязательными решениями и требуют проверки ответственным специалистом."


def checksum(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def csv_export(rows: list[dict]) -> bytes:
    output = io.StringIO()
    fields = ["organization", "region", "current_queue", "daily_forecast", "risk_level", "risk_score"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def xlsx_export(rows: list[dict]) -> bytes:
    """Create a compact standards-compliant XLSX without adding a runtime dependency."""
    headers = ["Организация", "Регион", "Текущая очередь", "Прогноз регистраций/день", "Риск", "Балл риска"]
    keys = ["organization", "region", "current_queue", "daily_forecast", "risk_level", "risk_score"]
    shared = headers + [str(row.get(key, "")) for row in rows for key in keys]
    unique = list(dict.fromkeys(shared))
    indexes = {value: index for index, value in enumerate(unique)}
    sheet_rows = []
    for row_idx, values in enumerate([headers] + [[row.get(k, "") for k in keys] for row in rows], 1):
        cells = []
        for col_idx, value in enumerate(values, 1):
            col, number = chr(64 + col_idx), isinstance(value, (int, float))
            cells.append(f'<c r="{col}{row_idx}"{("" if number else " t=\"s\"")}><v>{value if number else indexes[str(value)]}</v></c>')
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    strings = "".join(f"<si><t>{escape(value)}</t></si>" for value in unique)
    parts = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Отчёт" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>',
        "xl/sharedStrings.xml": f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared)}" uniqueCount="{len(unique)}">{strings}</sst>',
    }
    binary = io.BytesIO()
    with zipfile.ZipFile(binary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return binary.getvalue()


def printable_html(report: dict) -> str:
    snapshot = report["snapshot"]
    rows = "".join(
        f"<tr><td>{escape(str(row['organization']))}</td><td>{escape(str(row['region']))}</td><td>{row['current_queue']}</td><td>{row['daily_forecast']}</td><td>{escape(str(row['risk_level']))} ({row['risk_score']})</td></tr>"
        for row in snapshot.get("organizations", [])
    )
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>{report['id']}</title><style>@page{{size:A4;margin:16mm}}body{{font:13px Arial;color:#17324d}}h1{{font-size:24px}}.meta{{background:#eef5f7;padding:14px}}table{{width:100%;border-collapse:collapse;margin-top:18px}}th,td{{padding:7px;border:1px solid #ccd8df;text-align:left}}small{{color:#586d7c}}</style></head><body><h1>Управленческий отчёт MedFlow AI</h1><div class='meta'><b>Дата формирования:</b> {report['created_at']}<br><b>Отчёт:</b> {report['id']}<br><b>Период/фильтры:</b> {escape(json.dumps(report['filters'], ensure_ascii=False))}<br><b>Версия модели:</b> {escape(str(snapshot.get('model_version','—')))}<br><b>Актуальность данных:</b> {escape(str(snapshot.get('data_freshness','—')))}</div><h2>Ключевые показатели</h2><p>Текущая очередь: {snapshot.get('waiting','—')} · Организаций высокого риска: {snapshot.get('high_risk','—')} · Аномалий: {snapshot.get('anomalies','—')}</p><h2>Организации риска</h2><table><thead><tr><th>Организация</th><th>Регион</th><th>Текущая очередь</th><th>Прогноз/день</th><th>Риск</th></tr></thead><tbody>{rows}</tbody></table><h2>Действия</h2><p>{escape(str(snapshot.get('actions_summary','Нет зафиксированных действий в выбранном срезе.')))}</p><p><b>{DISCLAIMER}</b></p><hr><small>DEMO verification — не является ЭЦП: {report['checksum']}</small><script>window.addEventListener('load',()=>{{}})</script></body></html>"""


def pdf_export(report: dict) -> bytes:
    """Render the printable report with local LibreOffice; no remote service is used."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        raise RuntimeError("Local PDF renderer is unavailable")
    with tempfile.TemporaryDirectory(prefix="medflow-report-") as folder:
        source = Path(folder) / f"{report['id']}.html"
        source.write_text(printable_html(report), encoding="utf-8")
        environment = {
            **os.environ,
            "XDG_RUNTIME_DIR": folder,
            "XDG_CONFIG_HOME": folder,
            "XDG_CACHE_HOME": folder,
        }
        result = subprocess.run(
            [
                office,
                f"-env:UserInstallation=file://{folder}/profile",
                "--headless",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                folder,
                str(source),
            ],
            capture_output=True,
            env=environment,
            timeout=30,
            check=False,
        )
        output = source.with_suffix(".pdf")
        if result.returncode or not output.exists():
            raise RuntimeError("Local PDF renderer failed")
        return output.read_bytes()
