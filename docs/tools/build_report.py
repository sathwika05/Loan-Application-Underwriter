"""Render docs/report.md -> docs/report.html -> docs/report.pdf (headless Chrome/Edge).

Run from repo root:  backend\\.venv\\Scripts\\python docs\\tools\\build_report.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parents[1]
MD, HTML, PDF = DOCS / "report.md", DOCS / "report.html", DOCS / "report.pdf"

CSS = """
@page { size: A4; margin: 12mm 13mm 12mm 13mm; }
:root { --ink:#1f2937; --muted:#4b5563; --line:#cbd5e1; --head:#eef2ff; --accent:#3730a3; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Calibri, Arial, sans-serif; font-size: 8.2pt; line-height: 1.28;
       color: var(--ink); margin: 0; background: #fff; }
h1 { font-size: 14pt; margin: 0 0 .5mm; color: var(--accent); }
.meta { color: var(--muted); margin: 0 0 1.5mm; font-size: 8pt; }
h2 { font-size: 10pt; margin: 2.4mm 0 1mm; color: var(--accent); border-bottom: 1px solid var(--line);
     padding-bottom: .5mm; break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 1.2mm; }
table { width: 100%; border-collapse: collapse; margin: 0 0 1.2mm; font-size: 7.6pt; }
tr, td, th { break-inside: avoid; page-break-inside: avoid; }
th, td { border: 1px solid var(--line); padding: .35mm 1.1mm; vertical-align: top; text-align: left; }
th { background: var(--head); }
thead { display: table-header-group; }
code { font-family: Consolas, monospace; font-size: 7.3pt; background: #f1f5f9; padding: 0 .5mm; border-radius: 2px; }
figure { margin: 0; text-align: center; break-inside: avoid; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: 7.4pt; color: var(--muted); margin-top: .6mm; text-align: left; line-height: 1.25; }
.design { break-inside: avoid; page-break-inside: avoid; }
.pair { display: flex; gap: 4mm; align-items: flex-end; margin-bottom: 2mm; }
.pair .aws { flex: 0 0 60%; } .pair .lg { flex: 1 1 0; }
.pair img { max-height: 100mm; }
.er img { max-height: 80mm; }
.er figcaption { max-width: 120mm; margin: .6mm auto 0; }
"""


def build_html() -> None:
    body = markdown.markdown(MD.read_text(encoding="utf-8"), extensions=["tables", "md_in_html", "sane_lists"])
    HTML.write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Underwriter Routing Report</title><style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )


def find_browser() -> str:
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              shutil.which("chrome") or "", shutil.which("msedge") or ""]:
        if p and Path(p).exists():
            return p
    sys.exit("No Chrome/Edge found")


def build_pdf() -> None:
    if PDF.exists():
        PDF.unlink()
    subprocess.run([find_browser(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--allow-file-access-from-files", f"--print-to-pdf={PDF}", HTML.as_uri()],
                   check=False, capture_output=True, timeout=120)
    if not PDF.exists():
        sys.exit("PDF was not produced")


if __name__ == "__main__":
    build_html()
    build_pdf()
    print("wrote", HTML.name, "and", PDF.name, PDF.stat().st_size, "bytes")
