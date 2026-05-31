# QCM Solver

A desktop app that lets you snip parts of your screen (multiple-choice
questions), and a TypeScript middle-layer API that forwards those screenshots to
the Claude API with a predefined QCM-solving prompt and returns the answer.

```
┌──────────────────┐   HTTPS POST /api/solve  ┌──────────────────┐   Claude API   ┌─────────┐
│  Desktop app     │  (base64 PNG JSON)       │  Middle layer    │  (images +     │ Claude  │
│  (tkinter, mss)  │ ───────────────────────▶ │  (Vercel function│   QCM prompt)  │ Opus    │
│                  │ ◀─────────────────────── │   in TypeScript) │ ◀───────────── │         │
└──────────────────┘     JSON {answer}        └──────────────────┘    answer      └─────────┘
```

The desktop client never talks to Claude directly — it only knows your endpoint
URL. The API key lives only on the server (a Vercel environment variable).

> **Where does this run?** GitHub can't host the backend itself — GitHub Pages
> serves only static files and can't keep your API key secret or run server
> code. So the **code lives on GitHub**, and a serverless host **deploys from
> that repo**. Vercel is used here; Cloudflare Workers and Deno Deploy work the
> same way (connect the repo, set the secret, auto-deploy on push).

---

## 1. The middle layer (`api/`)

Two Vercel serverless functions written in TypeScript:

| Route | File | Purpose |
|-------|------|---------|
| `POST /api/solve` | `api/solve.ts` | Accepts screenshots, attaches the QCM prompt, calls Claude, returns the answer |
| `GET /api/health` | `api/health.ts` | Liveness check |

The QCM prompt is sent as a **cached system prompt**, so repeated requests are
cheaper. Claude runs with adaptive thinking and `effort: high` for accuracy.

### Deploy to Vercel (from GitHub)

1. Push this repo to GitHub.
2. On [vercel.com](https://vercel.com), **Add New → Project → Import** your repo.
   Vercel auto-detects the `api/` functions — no build config needed.
3. In **Project → Settings → Environment Variables**, add:
   - `ANTHROPIC_API_KEY` = your key (required)
   - `QCM_MODEL` (optional, default `claude-opus-4-8`)
   - `QCM_MAX_TOKENS` (optional, default `16000`)
4. Deploy. Your endpoint is `https://<your-project>.vercel.app/api/solve`.

Every push to the repo redeploys automatically.

Check it:

```bash
curl https://<your-project>.vercel.app/api/health
# {"status":"ok","model":"claude-opus-4-8"}
```

### Run it locally (optional)

```bash
npm install
npm i -g vercel          # the Vercel CLI
cp .env.example .env     # put your real ANTHROPIC_API_KEY in it
vercel dev               # serves http://localhost:3000/api/solve

npm run typecheck        # optional: type-check the functions
```

### API contract

`POST /api/solve` — JSON body:

```json
{ "images": [ { "media_type": "image/png", "data": "<base64>" } ] }
```

Response:

```json
{
  "answer": "Question 1: ...\nAnswer: B\n...",
  "image_count": 2,
  "model": "claude-opus-4-8",
  "request_id": "req_..."
}
```

> **Request size:** Vercel's Hobby plan caps the request body at ~4.5 MB.
> Cropped QCM screenshots are normally far smaller, but capture tight regions
> if you send many at once.

### Prefer a different host?

- **Cloudflare Workers** — no body-size friction, generous free tier. Would use
  a `fetch(request)` handler + `wrangler.toml`.
- **Deno Deploy** — TypeScript-native, deploys from GitHub.

The Claude call is identical on all three; only the handler wrapper changes. Ask
if you want one of these instead of Vercel.

---

## 2. The desktop app (`desktop/`)

Cross-platform (Windows / macOS / Linux) tkinter app.

### Run

```bash
cd desktop
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export QCM_ENDPOINT=https://<your-project>.vercel.app/api/solve
python qcm_app.py
```

> Tkinter ships with the standard Python installer on Windows and macOS. On
> some Linux distros install it separately, e.g. `sudo apt install python3-tk`.

### Use

1. Click **New** — the window hides and you drag-select a region of your
   screen. The crop is kept in memory. (Press **Esc** to cancel.)
2. The screenshot appears in the thumbnail carousel. Click **+ Add screenshot**
   to capture more, or **Remove** under any thumbnail to drop it.
3. Click **Send**. The app POSTs every screenshot (base64 JSON) to the endpoint,
   waits, and shows Claude's answer in the **Answer** panel.

The endpoint URL is editable at the top of the window (defaults to
`QCM_ENDPOINT`).

---

## Notes

- Set the model with `QCM_MODEL` (default `claude-opus-4-8`).
- The function allows up to 60s (`vercel.json` `maxDuration`) so hard questions
  have room to think; raise `QCM_MAX_TOKENS` if answers get cut off.
- This tool is meant for studying/learning. Use it accordingly.
