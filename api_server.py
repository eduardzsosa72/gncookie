"""
Amazon Cookie Gen — API Server para Railway
============================================
Endpoints:
  POST /generate          → { job_id }
  GET  /job/{id}          → { status, result? }
  GET  /health            → { ok }

Auth: header X-Secret con el valor de la env API_SECRET
"""

import os, uuid, asyncio, traceback, time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

# -- importar la lógica del generador -----------------------------------------
# main.py expone create_account() y devuelve el cookie_str cuando termina.
# Lo adaptamos para que retorne datos en vez de escribir a archivo.

import sys
sys.path.insert(0, os.path.dirname(__file__))

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_SECRET      = os.getenv("API_SECRET",      "cambiar_esto_en_railway")
MAX_CONCURRENT  = int(os.getenv("MAX_CONCURRENT", "3"))

# ── Estado en memoria ─────────────────────────────────────────────────────────
# { job_id: { status: "pending|running|done|error", result: {...}|None, error: str|None } }
JOBS: dict[str, dict] = {}
_SEMAPHORE: Optional[asyncio.Semaphore] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _SEMAPHORE
    _SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT)
    yield

app = FastAPI(title="Amazon CookieGen API", lifespan=lifespan)


# ── AUTH ───────────────────────────────────────────────────────────────────────
def check_auth(x_secret: str = Header(default=None)):
    if x_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── WORKER ────────────────────────────────────────────────────────────────────
async def run_job(job_id: str):
    global _SEMAPHORE
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["started_at"] = time.time()

    async with _SEMAPHORE:
        try:
            # Ejecutar create_account en un thread (es síncrono y usa curl_cffi)
            result = await asyncio.get_event_loop().run_in_executor(
                None, _run_create_account
            )
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = result
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            JOBS[job_id]["traceback"] = traceback.format_exc()

    JOBS[job_id]["finished_at"] = time.time()


def _run_create_account() -> dict:
    """
    Wrapper síncrono que llama a create_account() del main.py
    y retorna un dict con los datos de la cuenta creada.
    """
    # Importar aquí para no contaminar el scope global del servidor
    from main_api import create_account_api
    return create_account_api()


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "ok": True,
        "jobs": {
            "total":   len(JOBS),
            "pending": sum(1 for j in JOBS.values() if j["status"] == "pending"),
            "running": sum(1 for j in JOBS.values() if j["status"] == "running"),
            "done":    sum(1 for j in JOBS.values() if j["status"] == "done"),
            "error":   sum(1 for j in JOBS.values() if j["status"] == "error"),
        }
    }


@app.post("/generate")
async def generate(
    background_tasks: BackgroundTasks,
    x_secret: str = Header(default=None),
):
    check_auth(x_secret)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status":      "pending",
        "result":      None,
        "error":       None,
        "created_at":  time.time(),
        "started_at":  None,
        "finished_at": None,
    }
    background_tasks.add_task(run_job, job_id)
    return {"job_id": job_id}


@app.get("/job/{job_id}")
async def get_job(
    job_id: str,
    x_secret: str = Header(default=None),
):
    check_auth(x_secret)

    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")

    job = JOBS[job_id]
    response = {
        "job_id":      job_id,
        "status":      job["status"],
        "created_at":  job.get("created_at"),
        "started_at":  job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }

    if job["status"] == "done":
        response["result"] = job["result"]
    elif job["status"] == "error":
        response["error"] = job["error"]

    return response
