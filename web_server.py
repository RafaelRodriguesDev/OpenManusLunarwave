import asyncio
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


if Path("/usr/share/novnc").exists():
    app.mount("/vnc", StaticFiles(directory="/usr/share/novnc"), name="novnc")

BASE_DIR = Path(os.environ.get("OPENMANUS_DIR", "/app/OpenManus")).resolve()
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
WEB_DIR = Path(os.environ.get("WEB_DIR", "/app/OpenManus/web")).resolve()
NOVNC_URL = os.environ.get(
    "NOVNC_URL",
    "/vnc/vnc.html?autoconnect=true&resize=scale&path=vnc/websockify"
)

NOVNC_DIR = Path(os.environ.get("NOVNC_DIR", "/usr/share/novnc")).resolve()
VNC_HOST = os.environ.get("VNC_HOST", "127.0.0.1")
VNC_PORT = int(os.environ.get("VNC_PORT", "5900"))

jobs: Dict[str, Dict[str, Any]] = {}
subscribers: Dict[str, List[WebSocket]] = {}


class RunRequest(BaseModel):
    prompt: str


def utc_now() -> str:
    return datetime.utcnow().isoformat()


def create_job(prompt: str) -> Dict[str, Any]:
    job_id = str(uuid.uuid4())

    job = {
        "id": job_id,
        "prompt": prompt,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "logs": ["Job criado."],
        "result": "",
        "error": None,
        "exit_code": None,
        "steps": [
            {"name": "Receber tarefa", "status": "done"},
            {"name": "Preparar ambiente", "status": "pending"},
            {"name": "Iniciar OpenManus", "status": "pending"},
            {"name": "Executar tarefa", "status": "pending"},
            {"name": "Capturar resultado", "status": "pending"},
            {"name": "Finalizar", "status": "pending"},
        ],
    }

    jobs[job_id] = job
    subscribers[job_id] = []

    return job


async def notify(job_id: str) -> None:
    if job_id not in jobs:
        return

    dead_connections: List[WebSocket] = []

    for websocket in subscribers.get(job_id, []):
        try:
            await websocket.send_json(jobs[job_id])
        except Exception:
            dead_connections.append(websocket)

    for websocket in dead_connections:
        try:
            subscribers[job_id].remove(websocket)
        except ValueError:
            pass


async def add_log(job_id: str, message: str) -> None:
    job = jobs[job_id]
    job["logs"].append(message.rstrip())
    job["updated_at"] = utc_now()
    await notify(job_id)


async def set_status(job_id: str, status: str) -> None:
    job = jobs[job_id]
    job["status"] = status
    job["updated_at"] = utc_now()
    await notify(job_id)


async def set_step(job_id: str, step_name: str, status: str) -> None:
    job = jobs[job_id]

    for step in job["steps"]:
        if step["name"] == step_name:
            step["status"] = status
            break

    job["updated_at"] = utc_now()
    await notify(job_id)


async def run_openmanus_process(job_id: str, prompt: str) -> None:
    proc: Optional[asyncio.subprocess.Process] = None
    output_lines: List[str] = []

    try:
        await set_status(job_id, "running")

        await set_step(job_id, "Preparar ambiente", "running")
        await add_log(job_id, f"Diretório OpenManus: {BASE_DIR}")
        await add_log(job_id, f"Workspace: {WORKSPACE_DIR}")

        if not BASE_DIR.exists():
            raise RuntimeError(f"Diretório do OpenManus não encontrado: {BASE_DIR}")

        main_py = BASE_DIR / "main.py"

        if not main_py.exists():
            raise RuntimeError(f"Arquivo main.py não encontrado em: {main_py}")

        config_file = BASE_DIR / "config" / "config.toml"

        if not config_file.exists():
            raise RuntimeError(f"Arquivo config/config.toml não encontrado em: {config_file}")

        await set_step(job_id, "Preparar ambiente", "done")

        await set_step(job_id, "Iniciar OpenManus", "running")
        await add_log(job_id, "Iniciando processo: python -u main.py")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["WORKSPACE_DIR"] = str(WORKSPACE_DIR)
        env["DISPLAY"] = os.environ.get("DISPLAY", ":99")

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "main.py",
            cwd=str(BASE_DIR),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        await set_step(job_id, "Iniciar OpenManus", "done")

        await set_step(job_id, "Executar tarefa", "running")
        await add_log(job_id, "Enviando prompt para o OpenManus...")

        if proc.stdin is None:
            raise RuntimeError("stdin do processo não está disponível.")

        proc.stdin.write((prompt.strip() + "\n").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        if proc.stdout is None:
            raise RuntimeError("stdout do processo não está disponível.")

        while True:
            line = await proc.stdout.readline()

            if not line:
                break

            decoded = line.decode("utf-8", errors="replace").rstrip()
            output_lines.append(decoded)

            if decoded.strip():
                await add_log(job_id, decoded)

        exit_code = await proc.wait()

        jobs[job_id]["exit_code"] = exit_code

        await set_step(job_id, "Executar tarefa", "done")
        await set_step(job_id, "Capturar resultado", "running")

        final_output = "\n".join(output_lines).strip()

        if exit_code == 0:
            jobs[job_id]["result"] = final_output or "OpenManus finalizou sem saída textual."
            await add_log(job_id, "Processo finalizado com sucesso.")
            await set_step(job_id, "Capturar resultado", "done")
            await set_step(job_id, "Finalizar", "done")
            await set_status(job_id, "completed")
        else:
            jobs[job_id]["result"] = final_output
            jobs[job_id]["error"] = f"OpenManus finalizou com código {exit_code}."
            await add_log(job_id, f"Processo finalizado com erro. Exit code: {exit_code}")
            await set_step(job_id, "Capturar resultado", "failed")
            await set_step(job_id, "Finalizar", "failed")
            await set_status(job_id, "failed")

    except Exception as ex:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass

        jobs[job_id]["error"] = str(ex)
        jobs[job_id]["updated_at"] = utc_now()

        await add_log(job_id, f"Erro: {str(ex)}")

        await set_step(job_id, "Finalizar", "failed")
        await set_status(job_id, "failed")


def safe_workspace_path(relative_path: str) -> Path:
    requested_path = (WORKSPACE_DIR / relative_path).resolve()

    if not str(requested_path).startswith(str(WORKSPACE_DIR)):
        raise HTTPException(status_code=400, detail="Caminho inválido.")

    return requested_path


def list_workspace_files() -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []

    if not WORKSPACE_DIR.exists():
        return files

    for path in WORKSPACE_DIR.rglob("*"):
        if not path.is_file():
            continue

        try:
            stat = path.stat()
            files.append({
                "name": path.name,
                "path": str(path.relative_to(WORKSPACE_DIR)),
                "size": stat.st_size,
                "updated_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
            })
        except Exception:
            continue

    return sorted(files, key=lambda item: item["path"])


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    index_file = WEB_DIR / "index.html"

    if not index_file.exists():
        return f"""
        <h1>OpenManusWeb</h1>
        <p>Arquivo web/index.html não encontrado.</p>
        <p>WEB_DIR atual: {WEB_DIR}</p>
        """

    return index_file.read_text(encoding="utf-8")


@app.post("/api/run")
async def run_task(request: RunRequest) -> JSONResponse:
    prompt = request.prompt.strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt vazio.")

    job = create_job(prompt)

    asyncio.create_task(run_openmanus_process(job["id"], prompt))

    return JSONResponse({
        "job_id": job["id"],
        "status": job["status"],
    })


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    return jobs[job_id]


@app.websocket("/ws/jobs/{job_id}")
async def websocket_job(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()

    if job_id not in jobs:
        await websocket.send_json({"error": "Job não encontrado."})
        await websocket.close()
        return

    subscribers.setdefault(job_id, []).append(websocket)

    try:
        await websocket.send_json(jobs[job_id])

        while True:
            await asyncio.sleep(30)
            await websocket.send_json(jobs[job_id])

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            subscribers[job_id].remove(websocket)
        except Exception:
            pass


@app.get("/api/workspace/files")
async def workspace_files() -> Dict[str, Any]:
    return {
        "workspace": str(WORKSPACE_DIR),
        "files": list_workspace_files(),
    }


@app.get("/api/workspace/file")
async def workspace_file(path: str) -> PlainTextResponse:
    target = safe_workspace_path(path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    max_size = 1024 * 1024

    if target.stat().st_size > max_size:
        raise HTTPException(status_code=413, detail="Arquivo muito grande para preview.")

    content = target.read_text(encoding="utf-8", errors="replace")

    return PlainTextResponse(content)


@app.get("/preview")
async def preview():
    return RedirectResponse(url=NOVNC_URL)


@app.get("/api/config")
async def frontend_config() -> Dict[str, Any]:
    return {
        "novnc_url": NOVNC_URL,
        "workspace_dir": str(WORKSPACE_DIR),
        "web_dir": str(WEB_DIR),
        "vnc_host": VNC_HOST,
        "vnc_port": VNC_PORT,
    }

@app.websocket("/vnc/websockify")
async def vnc_websocket_proxy(websocket: WebSocket) -> None:
    await websocket.accept()

    reader = None
    writer = None

    try:
        reader, writer = await asyncio.open_connection(VNC_HOST, VNC_PORT)

        async def browser_to_vnc() -> None:
            while True:
                message = await websocket.receive()

                if message.get("type") == "websocket.disconnect":
                    break

                if "bytes" in message and message["bytes"] is not None:
                    writer.write(message["bytes"])
                    await writer.drain()

                elif "text" in message and message["text"] is not None:
                    writer.write(message["text"].encode("latin-1"))
                    await writer.drain()

        async def vnc_to_browser() -> None:
            while True:
                data = await reader.read(4096)

                if not data:
                    break

                await websocket.send_bytes(data)

        await asyncio.gather(browser_to_vnc(), vnc_to_browser())

    except WebSocketDisconnect:
        pass
    except Exception as ex:
        try:
            await websocket.send_text(f"VNC proxy error: {str(ex)}")
        except Exception:
            pass
    finally:
        try:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        except Exception:
            pass

        try:
            await websocket.close()
        except Exception:
            pass

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "base_dir": str(BASE_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
        "web_dir": str(WEB_DIR),
        "novnc_url": NOVNC_URL,
        "display": os.environ.get("DISPLAY", ""),
        "jobs_count": len(jobs),
    }