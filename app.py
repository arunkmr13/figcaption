"""
app.py — FigCaption Web Server (FastAPI)
"""
import os, sys, json, time, asyncio, threading, queue, tempfile
import zipfile
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse

sys.path.insert(0, str(Path(__file__).parent))
app = FastAPI(title="FigCaption")

UPLOAD_DIR = Path(tempfile.gettempdir()) / "figcaption_uploads"
OUTPUT_DIR = Path(tempfile.gettempdir()) / "figcaption_outputs"
FIGURES_DIR = Path(tempfile.gettempdir()) / "figcaption_figures"
for d in [UPLOAD_DIR, OUTPUT_DIR, FIGURES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

job_queues: dict = {}
job_results: dict = {}

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(Path(__file__).parent / "index.html").read_text())

@app.post("/upload")
async def upload(pdf: UploadFile = File(...), api_key: str = Form(default="")):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    contents = await pdf.read()
    job_id = f"job_{int(time.time()*1000)}"
    pdf_path = UPLOAD_DIR / f"{job_id}_{pdf.filename}"
    pdf_path.write_bytes(contents)
    q = queue.Queue()
    job_queues[job_id] = q
    threading.Thread(target=_run_pipeline, args=(job_id, str(pdf_path), api_key.strip()), daemon=True).start()
    return {"job_id": job_id}

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
            except:
                yield f"data: {json.dumps({'type':'ping'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/download/{job_id}")
async def download(job_id: str):
    result = job_results.get(job_id)
    if not result:
        raise HTTPException(404, "No output found")
    return FileResponse(path=result["excel_path"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=result["excel_filename"])

def _emit(job_id, msg):
    q = job_queues.get(job_id)
    if q: q.put(msg)

def _run_pipeline(job_id, pdf_path, api_key):
    try:
        from pipeline.extractor import extract_figures
        from pipeline.llm import generate_alt_text, DELAY_S
        from pipeline.output import write_excel, write_json
        pdf_name = Path(pdf_path).stem
        out = OUTPUT_DIR / job_id; out.mkdir(parents=True, exist_ok=True)
        figs_dir = FIGURES_DIR / job_id; figs_dir.mkdir(parents=True, exist_ok=True)
        _emit(job_id, {"type":"status","message":"Extracting figures..."})
        figures = extract_figures(pdf_path, output_dir=str(figs_dir))
        if not figures:
            _emit(job_id, {"type":"error","message":"No figures found."}); return
        _emit(job_id, {"type":"extracted","count":len(figures),"message":f"Found {len(figures)} figure(s). Generating alt text..."})
        for i, fig in enumerate(figures, 1):
            _emit(job_id, {"type":"progress","current":i,"total":len(figures),"fig_id":fig.fig_id,"fig_type":fig.fig_type,"caption":fig.caption[:120],"message":f"Processing {i}/{len(figures)}..."})
            alt, status = generate_alt_text(fig.image_bytes, fig.fig_type, fig.caption, api_key or os.getenv("GEMINI_API_KEY",""))
            fig.alt_text = alt; fig.status = status
            _emit(job_id, {"type":"figure_done","current":i,"total":len(figures),"fig_id":fig.fig_id,"fig_type":fig.fig_type,"caption":fig.caption[:200],"alt_text":alt,"status":status})
            if i < len(figures): time.sleep(DELAY_S)
        excel_path = out / f"{pdf_name}_alt_text.xlsx"
        write_excel(figures, str(excel_path))
        write_json(figures, str(out / f"{pdf_name}_alt_text.json"))
        job_results[job_id] = {"excel_path":str(excel_path),"excel_filename":excel_path.name,"total":len(figures),"done":sum(1 for f in figures if f.status=="done"),"skipped":sum(1 for f in figures if f.status=="skipped"),"errors":sum(1 for f in figures if "error" in f.status)}
        _emit(job_id, {"type":"done","job_id":job_id,**job_results[job_id]})
    except Exception as e:
        import traceback
        _emit(job_id, {"type":"error","message":str(e),"detail":traceback.format_exc()})

@app.get("/download-figures/{job_id}")
async def download_figures(job_id: str):
    figures_dir = FIGURES_DIR / job_id
    if not figures_dir.exists():
        raise HTTPException(404, "No figures found")
    
    zip_path = OUTPUT_DIR / job_id / "figures.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, "w") as zf:
        for img in sorted(figures_dir.glob("*.png")):
            zf.write(img, img.name)
    
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename="figures.zip"
    )