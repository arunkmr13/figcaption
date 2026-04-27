"""
app.py — FigCaption Web Server (Phase 2)
─────────────────────────────────────────
Accepts PDF and XML uploads. Outputs:
  - Excel (.xlsx)
  - Figures ZIP (.png images)
  - Alt Text XML (injected)
  - Embedded XML (base64 images inline)
"""

import os, sys, json, time, asyncio, threading, queue, tempfile, zipfile
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse

sys.path.insert(0, str(Path(__file__).parent))
app = FastAPI(title="FigCaption — Phase 2")

UPLOAD_DIR  = Path(tempfile.gettempdir()) / "figcaption_uploads"
OUTPUT_DIR  = Path(tempfile.gettempdir()) / "figcaption_outputs"
FIGURES_DIR = Path(tempfile.gettempdir()) / "figcaption_figures"
for d in [UPLOAD_DIR, OUTPUT_DIR, FIGURES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

job_queues:  dict = {}
job_results: dict = {}

SUPPORTED_EXTENSIONS = (".pdf", ".xml")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(Path(__file__).parent / "templates" / "index.html").read_text())


@app.post("/upload")
async def upload(pdf: UploadFile = File(...), api_key: str = Form(default="")):
    ext = Path(pdf.filename).suffix.lower()
    if ext not in (".pdf", ".xml"):
        raise HTTPException(400, f"Only PDF and XML files are supported (got {ext})")
    contents = await pdf.read()
    job_id = f"job_{int(time.time()*1000)}"
    pdf_path = UPLOAD_DIR / f"{job_id}_{pdf.filename}"
    pdf_path.write_bytes(contents)

    q = queue.Queue()
    job_queues[job_id] = q
    threading.Thread(
        target=_run_pipeline,
        args=(job_id, str(pdf_path), api_key.strip()),
        daemon=True,
    ).start()
    return {"job_id": job_id, "source_type": ext.lstrip(".")}


@app.get("/stream/{job_id}")
async def stream(job_id: str):
    q = job_queues.get(job_id)
    if not q:
        raise HTTPException(404, "Job not found")

    async def gen():
        loop = asyncio.get_event_loop()
        while True:
            try:
                msg = await loop.run_in_executor(None, lambda: q.get(timeout=60))
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") in ("done", "error"):
                    break
            except Exception:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/download/excel/{job_id}")
async def download_excel(job_id: str):
    result = job_results.get(job_id)
    if not result or not result.get("excel_path"):
        raise HTTPException(404, "No Excel output found")
    return FileResponse(
        path=result["excel_path"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=Path(result["excel_path"]).name,
    )


@app.get("/download/figures/{job_id}")
async def download_figures(job_id: str):
    figures_dir = FIGURES_DIR / job_id
    print(f"Looking for figures in: {figures_dir}")
    print(f"Exists: {figures_dir.exists()}")
    if figures_dir.exists():
        print(f"Files: {list(figures_dir.glob('*.png'))}")
    
    if not figures_dir.exists():
        raise HTTPException(404, "No figures found")
    
    zip_path = OUTPUT_DIR / job_id / "figures.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        png_files = sorted(figures_dir.glob("*.png"))
        print(f"Adding {len(png_files)} files to zip")
        for img in png_files:
            zf.write(img, img.name)
    
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename="figures.zip"
    )


@app.get("/download/xml/{job_id}")
async def download_xml(job_id: str):
    result = job_results.get(job_id)
    if not result or not result.get("alt_xml_path"):
        raise HTTPException(404, "No XML output found (PDF input does not produce XML)")
    return FileResponse(
        path=result["alt_xml_path"],
        media_type="application/xml",
        filename=Path(result["alt_xml_path"]).name,
    )


@app.get("/download/embedded-xml/{job_id}")
async def download_embedded_xml(job_id: str):
    result = job_results.get(job_id)
    if not result or not result.get("embedded_xml_path"):
        raise HTTPException(404, "No embedded XML found (PDF input does not produce XML)")
    return FileResponse(
        path=result["embedded_xml_path"],
        media_type="application/xml",
        filename=Path(result["embedded_xml_path"]).name,
    )


# ── Pipeline runner (background thread) ──────────────────────────────────────
def _emit(job_id, msg):
    q = job_queues.get(job_id)
    if q:
        q.put(msg)


def _run_pipeline(job_id: str, file_path: str, api_key: str):
    try:
        from pipeline.extractor import extract_figures
        from pipeline.llm import generate_alt_text, DELAY_S
        from pipeline.output import write_excel, write_json, print_metrics
        from pipeline.xml_writer import write_xml_outputs

        file_path = Path(file_path)
        source_type = file_path.suffix.lower().lstrip(".")
        stem = file_path.stem

        out_dir  = OUTPUT_DIR / job_id;  out_dir.mkdir(parents=True, exist_ok=True)
        figs_dir = FIGURES_DIR / job_id; figs_dir.mkdir(parents=True, exist_ok=True)

        _emit(job_id, {"type": "status", "message": f"Extracting figures from {source_type.upper()}..."})
        figures = extract_figures(str(file_path), output_dir=str(figs_dir))

        if not figures:
            _emit(job_id, {"type": "error", "message": "No figures found."}); return

        eligible = [f for f in figures if f.eligible]
        _emit(job_id, {
            "type": "extracted",
            "count": len(figures),
            "eligible": len(eligible),
            "message": f"Found {len(figures)} figure(s), {len(eligible)} eligible for alt text generation.",
        })

        for i, fig in enumerate(figures, 1):
            _emit(job_id, {
                "type": "progress",
                "current": i, "total": len(figures),
                "fig_id": fig.fig_id,
                "fig_type": fig.fig_type,
                "caption": fig.caption[:120],
                "final_flag": fig.final_flag,
                "eligible": fig.eligible,
                "message": f"Processing {i}/{len(figures)}...",
            })

            if not fig.eligible:
                fig.alt_text = f"[SKIPPED — {fig.final_flag}]"
                fig.status = "skipped"
            else:
                alt, status = generate_alt_text(
                    image_bytes=fig.image_bytes,
                    fig_type=fig.fig_type,
                    caption=fig.caption,
                    final_flag=fig.final_flag,
                    existing_alt=fig.existing_alt,
                    api_key=api_key or os.getenv("GEMINI_API_KEY", ""),
                )
                fig.alt_text = alt
                fig.status = status
                if i < len(figures):
                    time.sleep(DELAY_S)

            _emit(job_id, {
                "type": "figure_done",
                "current": i, "total": len(figures),
                "fig_id": fig.fig_id,
                "fig_type": fig.fig_type,
                "caption": fig.caption[:200],
                "final_flag": fig.final_flag,
                "confidence": str(fig.confidence),
                "alt_text": fig.alt_text,
                "status": fig.status,
            })

        # Write outputs
        excel_path = out_dir / f"{stem}_alt_text.xlsx"
        write_excel(figures, str(excel_path))
        write_json(figures, str(out_dir / f"{stem}_alt_text.json"))

        alt_xml_path = ""
        embedded_xml_path = ""
        if source_type == "xml":
            xml_paths = write_xml_outputs(
                xml_path=str(file_path),
                figures=figures,
                output_dir=str(out_dir),
            )
            alt_xml_path      = xml_paths.get("alt_text_xml", "")
            embedded_xml_path = xml_paths.get("embedded_xml", "")

        result = {
            "excel_path":        str(excel_path),
            "alt_xml_path":      alt_xml_path,
            "embedded_xml_path": embedded_xml_path,
            "source_type":       source_type,
            "total":             len(figures),
            "done":              sum(1 for f in figures if f.status == "done"),
            "skipped":           sum(1 for f in figures if "skip" in f.status),
            "errors":            sum(1 for f in figures if "error" in f.status.lower()),
        }
        job_results[job_id] = result
        _emit(job_id, {"type": "done", "job_id": job_id, **result})

    except Exception as e:
        import traceback
        _emit(job_id, {"type": "error", "message": str(e), "detail": traceback.format_exc()})