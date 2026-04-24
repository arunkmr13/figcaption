# APS Alt Text Generation Pipeline — Phase 1

Automated pipeline to extract figures from scientific PDFs, associate captions,
and generate accessible alt text using Google Gemini 2.5 Pro.

---

## Project Structure

```
aps_pipeline/
├── main.py                  ← Entry point (run this)
├── requirements.txt
├── pipeline/
│   ├── extractor.py         ← PDF parsing, figure extraction, caption matching
│   ├── llm.py               ← Gemini 2.5 Pro alt text generation
│   └── output.py            ← Excel + JSON output
├── figures/                 ← Extracted figure images (auto-created)
└── output/                  ← Excel + JSON results (auto-created)
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get a Gemini API key

1. Go to https://aistudio.google.com/app/apikey
2. Create a new API key
3. Set it as an environment variable:

```bash
# macOS / Linux
export GEMINI_API_KEY="your-key-here"

# Windows (Command Prompt)
set GEMINI_API_KEY=your-key-here

# Windows (PowerShell)
$env:GEMINI_API_KEY="your-key-here"
```

---

## Usage

### Process a single PDF

```bash
python main.py path/to/paper.pdf
```

### Process all PDFs in a folder

```bash
python main.py path/to/papers/
```

### Dry run (extract figures only, skip LLM)

Useful for checking extraction quality before spending API credits.

```bash
python main.py paper.pdf --dry-run
```

### Pass API key directly

```bash
python main.py paper.pdf --api-key "your-key-here"
```

### Skip JSON output

```bash
python main.py paper.pdf --no-json
```

### Custom output directories

```bash
python main.py paper.pdf --output-dir results/ --figures-dir extracted_images/
```

---

## Output

### Excel file (`output/<pdf_name>_alt_text.xlsx`)

**Sheet: Alt Text** — one row per figure

| Column    | Description                          |
|-----------|--------------------------------------|
| pdf       | Source PDF filename                  |
| page      | Page number                          |
| fig_id    | Unique figure identifier             |
| fig_type  | Classified type (Line graph, etc.)   |
| image     | Saved image filename                 |
| caption   | Extracted caption text               |
| alt_text  | Generated alt text                   |
| status    | `done` / `skipped` / `error`         |

**Sheet: Summary** — counts by type and status

### JSON file (`output/<pdf_name>_alt_text.json`)

One object per figure:

```json
[
  {
    "pdf": "paper.pdf",
    "page": 3,
    "fig_id": "paper_p003_f001",
    "fig_type": "Line graph",
    "image": "paper_p003_f001.png",
    "caption": "Figure 1. Treatment response over time...",
    "alt_text": "Line graph showing increasing treatment response over 12 weeks, with a significant improvement after week 8.",
    "status": "done"
  }
]
```

---

## How It Works

```
PDF
 │
 ├─ extractor.py
 │   ├─ Page iteration (PyMuPDF)
 │   ├─ Embedded image extraction → filter by size (≥200px)
 │   ├─ Fallback: full-page render (captures vector figures)
 │   ├─ Caption association (spatial proximity, ≤150px below image)
 │   └─ Figure type classification (keyword rules)
 │
 ├─ llm.py
 │   ├─ Build prompt with figure type + caption context
 │   ├─ Send image + prompt to Gemini 2.5 Pro
 │   ├─ Retry on transient errors (3 attempts)
 │   └─ Post-process: clean fillers, limit to 2 sentences
 │
 └─ output.py
     ├─ Excel: Alt Text sheet + Summary sheet
     └─ JSON: per-figure records
```

---

## Configuration

Edit constants at the top of each module:

| Parameter         | File          | Default          | Description                         |
|-------------------|---------------|------------------|-------------------------------------|
| `MIN_IMG_PX`      | extractor.py  | `200`            | Min image width/height (px)         |
| `CAPTION_V_THRESHOLD` | extractor.py | `150`         | Max px below image to find caption  |
| `MODEL`           | llm.py        | `gemini-2.5-pro` | Gemini model to use                 |
| `DELAY_S`         | llm.py        | `1.0`            | Seconds between API calls           |
| `MAX_RETRIES`     | llm.py        | `3`              | Retries on API failure              |

---

## Known Limitations (Phase 1)

- Caption matching is heuristic — may misassociate on multi-column or complex layouts
- Vector-only pages fall back to full-page render (less granular)
- Tables not fully supported (pending scope decision)
- Multi-figure panel layouts may not split correctly

## Phase 2 (next)

- Automated QC: length validation, filler phrase detection, missing-insight flagging

## Phase 3 (deferred)

- PDF accessibility tag injection (Adobe SDK / Python evaluation)