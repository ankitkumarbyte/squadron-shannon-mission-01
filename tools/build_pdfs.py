#!/usr/bin/env python3
"""Build the three Moodle PDFs (Moodle accepts PDFs only, max 3 files).

  submission/Mission01_SquadronShannon_1_Report.pdf          cover, README overview, reports 01-03
  submission/Mission01_SquadronShannon_2_Code_and_Tests.pdf  04_dialogic_engine.py + tests
  submission/Mission01_SquadronShannon_3_Slides_and_Transcript.pdf  presentation slides + 05 transcript

Uses headless Google Chrome for HTML -> PDF (mermaid diagrams are rendered by Chrome).
Needs: pip install markdown pypdf pygments   (plus Google Chrome)
Run:  python tools/build_pdfs.py
"""

import datetime as dt
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import PythonLexer
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submission"
REPO = "https://github.com/ankitkumarbyte/squadron-shannon-mission-01"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #111; }
h1 { font-size: 20pt; border-bottom: 2px solid #0369a1; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 14pt; color: #0b3b66; margin-top: 18px; }
h3 { font-size: 12pt; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.2pt; page-break-inside: avoid; }
th, td { border: 1px solid #c9d3e3; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eef3fb; }
code { font-family: Menlo, Consolas, monospace; font-size: 8.8pt; background: #f2f5fa; padding: 0 3px; border-radius: 3px; }
pre { background: #f6f8fb; border: 1px solid #dfe5ef; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; font-size: 8.6pt; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
pre.mermaid { background: #fff; border: 0; text-align: center; }
blockquote { border-left: 3px solid #0369a1; margin: 8px 0; padding: 2px 12px; color: #333; background: #f4f8fd; }
a { color: #0369a1; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ccd6e6; margin: 14px 0; }
.doc { page-break-before: always; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; }
.cover .kicker { font-family: Menlo, monospace; letter-spacing: .15em; color: #0369a1; font-size: 9pt; text-transform: uppercase; }
.cover h1 { font-size: 30pt; border: 0; margin: 8px 0 4px; }
.cover .sub { font-size: 13pt; color: #333; margin-bottom: 24px; }
.cover table { width: auto; font-size: 10.5pt; }
.cover td { border: 0; padding: 3px 14px 3px 0; }
.toc li { margin: 2px 0; }
.src { font-size: 7.6pt; }
.src pre { page-break-inside: auto; font-size: 7.6pt; line-height: 1.35; }
.src .linenos { color: #8a94a6; padding-right: 6px; user-select: none; }
.src .highlight pre { page-break-inside: auto; white-space: pre-wrap; }
"""


def md_to_html(text: str) -> str:
    # Turn ```mermaid blocks into <pre class="mermaid"> so mermaid.js renders them.
    text = re.sub(r"```mermaid\n(.*?)```", lambda m: '<pre class="mermaid">' + html.escape(m.group(1)) + "</pre>",
                  text, flags=re.S)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    # Relative repo links -> absolute GitHub links (a PDF has no folder next to it).
    body = re.sub(r'href="(?!https?:|#|mailto:)([^"]+)"', lambda m: f'href="{REPO}/blob/main/{m.group(1)}"', body)
    return body


def page(title: str, body: str, extra_css: str = "", mermaid: bool = False) -> str:
    script = (f'<script src="{MERMAID}"></script><script>mermaid.initialize({{startOnLoad:true, theme:"default",'
              f' flowchart:{{useMaxWidth:true}}, stateDiagram:{{useMaxWidth:true}}}});</script>') if mermaid else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}{extra_css}</style>{script}</head><body>{body}</body></html>")


def run_chrome(args: list[str], out: Path, timeout: float = 120) -> None:
    """Run headless Chrome until `out` exists and stops growing (Chrome's updater can keep it alive)."""
    import time
    out.unlink(missing_ok=True)
    proc = subprocess.Popen([CHROME, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    start, last = time.time(), -1
    try:
        while time.time() - start < timeout:
            time.sleep(1.0)
            if out.exists():
                size = out.stat().st_size
                if size > 0 and size == last:
                    return
                last = size
            if proc.poll() is not None and out.exists():
                return
        raise TimeoutError(f"Chrome did not produce {out.name} within {timeout}s")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()


def chrome_pdf(html_text: str, out: Path, landscape_png: bool = False) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(html_text, encoding="utf-8")
        run_chrome(["--headless=new", "--disable-gpu", f"--user-data-dir={tmp}/profile",
                    "--no-pdf-header-footer", "--virtual-time-budget=15000",
                    f"--print-to-pdf={out}", src.as_uri()], out)


def cover() -> str:
    today = dt.date.today().isoformat()
    return f"""<div class="cover">
 <div class="kicker">14013 Conversational AI: History, Applications, Future · BTU Cottbus-Senftenberg · WS 2026/27</div>
 <h1>Mission 01 — The Architecture of Argument</h1>
 <div class="sub">Dialogic protocols from Socrates to multi-agent AI: a Socratic state machine and a Galilean 3-persona debate, run against a live LLM</div>
 <table>
  <tr><td><b>Squadron</b></td><td>Squadron Shannon</td></tr>
  <tr><td><b>Station officers</b></td><td>Ankit Kumar &amp; Garima Swami (all four stations)</td></tr>
  <tr><td><b>Group members</b></td><td>Ankit Kumar · Garima Swami · Abdul Haleem Haseeb</td></tr>
  <tr><td><b>Repository</b></td><td><a href="{REPO}">{REPO.replace('https://', '')}</a></td></tr>
  <tr><td><b>Date</b></td><td>{today}</td></tr>
 </table>
 <h3 style="margin-top:28px">Contents of this submission</h3>
 <ol class="toc">
  <li><b>PDF 1 · Report:</b> overview (README) · 01 Alignment Brief · 02 Socratic Protocol Map · 03 Galilean Sandbox</li>
  <li><b>PDF 2 · Code &amp; tests:</b> 04_dialogic_engine.py · tests/test_dialogic_engine.py</li>
  <li><b>PDF 3 · Slides &amp; transcript:</b> interactive presentation (static pages) · 05_Execution_Transcript.txt</li>
 </ol>
 <p style="margin-top:18px;color:#444">The notebook (<code>ps01_solution.ipynb</code>), JSON-RPC trace, raw logs and the interactive presentation are in the repository.</p>
</div>"""


def build_report(out: Path) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    parts = [cover()]
    for name in ("README.md", "01_Alignment_Brief.md", "02_Socratic_Protocol_Map.md", "03_Galilean_Sandbox.md"):
        text = readme if name == "README.md" else (ROOT / name).read_text(encoding="utf-8")
        parts.append(f'<section class="doc">{md_to_html(text)}</section>')
    chrome_pdf(page("Mission 01 Report — Squadron Shannon", "".join(parts), mermaid=True), out)


def build_code(out: Path) -> None:
    fmt = HtmlFormatter(linenos="inline", style="friendly")
    parts = [f"<h1>Mission 01 — Code &amp; Tests</h1><p>Squadron Shannon · source as in "
             f"<a href='{REPO}'>{REPO.replace('https://', '')}</a>. Run <code>python -m pytest -q</code> "
             f"for the offline test suite.</p>"]
    for rel in ("04_dialogic_engine.py", "tests/test_dialogic_engine.py"):
        code = (ROOT / rel).read_text(encoding="utf-8")
        parts.append(f'<section class="{"doc " if rel.startswith("tests") else ""}src"><h2>{rel}</h2>'
                     f"{highlight(code, PythonLexer(), fmt)}</section>")
    chrome_pdf(page("Mission 01 Code — Squadron Shannon", "".join(parts), fmt.get_style_defs(".highlight")), out)


def build_slides(tmpdir: Path) -> Path:
    """Screenshot every slide of the presentation (print mode) into a landscape PDF."""
    pres = ROOT / "presentation" / "Mission01_Presentation_standalone.html"
    total = len(re.findall(r'<section class="slide"', pres.read_text(encoding="utf-8")))
    shots = []
    for k in range(1, total + 1):
        png = tmpdir / f"slide{k:02d}.png"
        run_chrome(["--headless=new", "--disable-gpu", f"--user-data-dir={tmpdir}/p{k}",
                    "--hide-scrollbars", "--window-size=1600,900", "--virtual-time-budget=6000",
                    f"--screenshot={png}", pres.as_uri() + f"?print=1#{k}"], png)
        shots.append(png)
    imgs = "".join(f'<div class="s"><img src="{p.as_uri()}"></div>' for p in shots)
    css = "@page{size:297mm 167mm;margin:0} body{margin:0} .s{page-break-after:always;height:167mm;display:flex;align-items:center;justify-content:center;background:#050913} .s img{width:100%}"
    out = tmpdir / "slides.pdf"
    html_text = f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{imgs}</body></html>"
    src = tmpdir / "slides.html"
    src.write_text(html_text, encoding="utf-8")
    run_chrome(["--headless=new", "--disable-gpu", f"--user-data-dir={tmpdir}/pp", "--no-pdf-header-footer",
                "--allow-file-access-from-files", f"--print-to-pdf={out}", src.as_uri()], out)
    return out


def build_transcript(out: Path) -> None:
    text = (ROOT / "05_Execution_Transcript.txt").read_text(encoding="utf-8")
    body = (f"<h1>05 — Execution Transcript (live run)</h1><p>Unedited output of <code>04_dialogic_engine.py</code> "
            f"against the Gemini API, 2026-10-10. JSON-RPC trace: <code>logs/rpc_trace.jsonl</code> in the repository.</p>"
            f"<pre style='font-size:7.8pt;page-break-inside:auto'>{html.escape(text)}</pre>")
    chrome_pdf(page("Mission 01 Transcript — Squadron Shannon", body), out)


def merge(pdfs: list[Path], out: Path) -> None:
    w = PdfWriter()
    for p in pdfs:
        for pg in PdfReader(p).pages:
            w.add_page(pg)
    w.add_metadata({"/Title": out.stem.replace("_", " "), "/Author": "Squadron Shannon (Ankit Kumar, Garima Swami)"})
    with out.open("wb") as fh:
        w.write(fh)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        rep = tmp / "report.pdf"; build_report(rep)
        merge([rep], OUT / "Mission01_SquadronShannon_1_Report.pdf")
        code = tmp / "code.pdf"; build_code(code)
        merge([code], OUT / "Mission01_SquadronShannon_2_Code_and_Tests.pdf")
        slides = build_slides(tmp)
        tr = tmp / "transcript.pdf"; build_transcript(tr)
        merge([slides, tr], OUT / "Mission01_SquadronShannon_3_Slides_and_Transcript.pdf")
    for p in sorted(OUT.glob("*.pdf")):
        print(f"{p.name}: {len(PdfReader(p).pages)} pages, {p.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
