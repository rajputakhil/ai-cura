# Deploying AI-CURA to a public URL

The app runs on **Streamlit Community Cloud** (free) so you can share a live link
(e.g. in your GitHub README or with an interviewer).

## What works where

| Feature | Local | Streamlit Cloud |
|---|---|---|
| Automated ACMG criteria (VEP, gnomAD, ClinVar) | ✅ | ✅ |
| Claude API interpretation | ✅ (with key) | ✅ (with key in Secrets) |
| Local DeepSeek / Ollama models | ✅ | ❌ (no Ollama server in the cloud) |
| Paper upload → literature scoring | ✅ | ✅ (uses Claude if key set) |

The local-model comparison stays a **local-only** feature — worth mentioning as the
"runs offline / no data leaves the machine" angle, which matters for clinical data.

## Steps

1. **Push to GitHub** (public or private repo):
   ```bash
   cd ai-cura-prototype
   git init
   git add .
   git commit -m "AI-CURA prototype"
   git branch -M main
   git remote add origin https://github.com/<you>/ai-cura.git
   git push -u origin main
   ```
   `.env` and `.streamlit/secrets.toml` are gitignored — your keys stay private.

2. **Deploy**: go to https://share.streamlit.io → *New app* → pick your repo,
   branch `main`, main file `app.py`.

3. **Add your key** (optional, for Claude interpretation): App → *Settings* → *Secrets* →
   paste:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```

4. **Done** — you get a URL like `https://<you>-ai-cura.streamlit.app`.

## Notes
- Ensembl VEP / gnomAD / ClinVar are public APIs and work from the cloud.
- If a build fails, check that `requirements.txt` is at the repo root next to `app.py`.
