import asyncio
import html
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel


app = FastAPI(title="OpenManusWeb", version="0.2.0")

BASE_DIR = Path(os.environ.get("OPENMANUS_DIR", "/app/OpenManus")).resolve()
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()

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
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8" />
    <title>OpenManusWeb</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background: #060914;
            color: #e5e7eb;
            font-family: Inter, Arial, sans-serif;
        }

        .app {
            height: 100vh;
            display: grid;
            grid-template-columns: 380px 1fr;
            overflow: hidden;
        }

        .sidebar {
            background: linear-gradient(180deg, #0b1020, #060914);
            border-right: 1px solid #1f2937;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }

        .brand {
            padding: 22px;
            border-bottom: 1px solid #1f2937;
        }

        .brand h1 {
            margin: 0;
            font-size: 22px;
            letter-spacing: -0.03em;
        }

        .brand p {
            margin: 8px 0 0;
            color: #94a3b8;
            font-size: 13px;
        }

        .chat {
            flex: 1;
            padding: 18px;
            overflow: auto;
        }

        .bubble {
            padding: 14px;
            border-radius: 16px;
            margin-bottom: 12px;
            line-height: 1.45;
            font-size: 14px;
            white-space: pre-wrap;
        }

        .bubble.system {
            background: #111827;
            border: 1px solid #1f2937;
            color: #cbd5e1;
        }

        .bubble.user {
            background: linear-gradient(135deg, #1d4ed8, #7c3aed);
            color: white;
        }

        .composer {
            padding: 18px;
            border-top: 1px solid #1f2937;
            background: #0b1020;
        }

        textarea {
            width: 100%;
            height: 130px;
            resize: none;
            background: #020617;
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 14px;
            padding: 14px;
            outline: none;
            font-size: 14px;
        }

        textarea:focus {
            border-color: #60a5fa;
        }

        button {
            margin-top: 12px;
            width: 100%;
            border: none;
            border-radius: 14px;
            padding: 13px 16px;
            background: linear-gradient(135deg, #2563eb, #7c3aed);
            color: white;
            font-weight: 700;
            cursor: pointer;
            font-size: 14px;
        }

        button:disabled {
            background: #475569;
            cursor: not-allowed;
        }

        .main {
            display: grid;
            grid-template-rows: 72px 1fr 260px;
            min-width: 0;
            overflow: hidden;
        }

        .topbar {
            padding: 18px 24px;
            border-bottom: 1px solid #1f2937;
            background: #090e1a;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .topbar-title {
            font-size: 18px;
            font-weight: 800;
            letter-spacing: -0.02em;
        }

        .status-pill {
            padding: 8px 12px;
            border-radius: 999px;
            background: #111827;
            border: 1px solid #334155;
            color: #93c5fd;
            font-size: 13px;
        }

        .grid {
            display: grid;
            grid-template-columns: minmax(0, 1.1fr) 420px;
            overflow: hidden;
        }

        .panel {
            padding: 22px;
            overflow: auto;
            border-right: 1px solid #1f2937;
            min-width: 0;
        }

        .panel:last-child {
            border-right: none;
        }

        .panel h2 {
            margin: 0 0 16px;
            font-size: 16px;
            color: #f8fafc;
        }

        .timeline {
            display: grid;
            gap: 12px;
        }

        .step {
            background: #0f172a;
            border: 1px solid #1f2937;
            border-radius: 16px;
            padding: 14px;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .dot {
            width: 13px;
            height: 13px;
            border-radius: 999px;
            background: #64748b;
            flex: 0 0 auto;
        }

        .step.done .dot {
            background: #22c55e;
        }

        .step.running .dot {
            background: #3b82f6;
            box-shadow: 0 0 18px #3b82f6;
        }

        .step.failed .dot {
            background: #ef4444;
        }

        .step-name {
            font-weight: 800;
        }

        .step-status {
            color: #94a3b8;
            font-size: 13px;
            margin-top: 3px;
        }

        .result-box {
            background: #020617;
            border: 1px solid #334155;
            border-radius: 16px;
            min-height: 230px;
            padding: 16px;
            white-space: pre-wrap;
            line-height: 1.5;
            color: #dbeafe;
            overflow: auto;
        }

        .files {
            display: grid;
            gap: 10px;
        }

        .file {
            background: #0f172a;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 12px;
            font-size: 13px;
        }

        .file strong {
            display: block;
            color: #f8fafc;
            margin-bottom: 4px;
            word-break: break-all;
        }

        .file span {
            color: #94a3b8;
        }

        .file a {
            color: #93c5fd;
            text-decoration: none;
            display: inline-block;
            margin-top: 8px;
        }

        .logs {
            background: #020617;
            border-top: 1px solid #1f2937;
            padding: 18px 24px;
            overflow: auto;
            font-family: Consolas, monospace;
            font-size: 13px;
            color: #cbd5e1;
            white-space: pre-wrap;
        }

        .empty {
            color: #64748b;
        }

        @media (max-width: 980px) {
            .app {
                grid-template-columns: 1fr;
            }

            .sidebar {
                display: none;
            }

            .grid {
                grid-template-columns: 1fr;
            }

            .main {
                grid-template-rows: 72px 1fr 240px;
            }
        }
    </style>
</head>
<body>
    <div class="app">
        <aside class="sidebar">
            <div class="brand">
                <h1>OpenManusWeb</h1>
                <p>Interface web para executar o OpenManus na VPS</p>
            </div>

            <div class="chat" id="chat">
                <div class="bubble system">
                    Pronto. Envie uma tarefa para o agente executar.
                </div>
            </div>

            <div class="composer">
                <textarea id="prompt" placeholder="Ex: Crie um relatório sobre X, gere uma landing page, pesquise algo, analise um site..."></textarea>
                <button id="runBtn" onclick="runTask()">Executar tarefa</button>
            </div>
        </aside>

        <main class="main">
            <header class="topbar">
                <div class="topbar-title">Painel de execução</div>
                <div class="status-pill">Status: <span id="status">aguardando</span></div>
            </header>

            <section class="grid">
                <div class="panel">
                    <h2>Fluxo do agente</h2>
                    <div class="timeline" id="timeline">
                        <div class="empty">Nenhuma execução ainda.</div>
                    </div>

                    <h2 style="margin-top: 24px;">Resultado</h2>
                    <div class="result-box" id="result">O resultado final aparecerá aqui.</div>
                </div>

                <div class="panel">
                    <h2>Workspace</h2>
                    <div class="files" id="files">
                        <div class="empty">Nenhum arquivo listado ainda.</div>
                    </div>
                </div>
            </section>

            <section class="logs" id="logs">Logs aparecerão aqui.</section>
        </main>
    </div>

    <script>
        let currentJobId = null;
        let socket = null;

        function appendChat(type, text) {
            const chat = document.getElementById("chat");
            const div = document.createElement("div");
            div.className = "bubble " + type;
            div.innerText = text;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }

        function renderJob(job) {
            document.getElementById("status").innerText = job.status || "desconhecido";

            const timeline = document.getElementById("timeline");
            timeline.innerHTML = "";

            if (job.steps && job.steps.length) {
                job.steps.forEach(step => {
                    const div = document.createElement("div");
                    div.className = "step " + step.status;
                    div.innerHTML = `
                        <div class="dot"></div>
                        <div>
                            <div class="step-name">${escapeHtml(step.name)}</div>
                            <div class="step-status">${escapeHtml(step.status)}</div>
                        </div>
                    `;
                    timeline.appendChild(div);
                });
            }

            document.getElementById("logs").innerText = (job.logs || []).join("\\n");

            if (job.result) {
                document.getElementById("result").innerText = job.result;
            }

            if (job.error) {
                document.getElementById("result").innerText = job.error;
            }

            if (job.status === "completed" || job.status === "failed") {
                document.getElementById("runBtn").disabled = false;
                loadFiles();
            }

            const logs = document.getElementById("logs");
            logs.scrollTop = logs.scrollHeight;
        }

        function escapeHtml(value) {
            return String(value)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;")
                .replaceAll("'", "&#039;");
        }

        async function runTask() {
            const promptInput = document.getElementById("prompt");
            const prompt = promptInput.value.trim();

            if (!prompt) {
                alert("Digite uma tarefa primeiro.");
                return;
            }

            document.getElementById("runBtn").disabled = true;
            document.getElementById("result").innerText = "";
            document.getElementById("logs").innerText = "Criando job...";

            appendChat("user", prompt);
            appendChat("system", "Executando tarefa...");

            const response = await fetch("/api/run", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({prompt})
            });

            if (!response.ok) {
                const text = await response.text();
                document.getElementById("logs").innerText = text;
                document.getElementById("runBtn").disabled = false;
                return;
            }

            const data = await response.json();
            currentJobId = data.job_id;

            connectSocket(currentJobId);
        }

        function connectSocket(jobId) {
            if (socket) {
                socket.close();
            }

            const protocol = window.location.protocol === "https:" ? "wss" : "ws";
            socket = new WebSocket(`${protocol}://${window.location.host}/ws/jobs/${jobId}`);

            socket.onmessage = function(event) {
                const job = JSON.parse(event.data);
                renderJob(job);
            };

            socket.onerror = function() {
                document.getElementById("logs").innerText += "\\nErro no WebSocket.";
            };

            socket.onclose = function() {
                console.log("WebSocket fechado.");
            };
        }

        async function loadFiles() {
            const response = await fetch("/api/workspace/files");
            const data = await response.json();

            const files = document.getElementById("files");
            files.innerHTML = "";

            if (!data.files || !data.files.length) {
                files.innerHTML = `<div class="empty">Nenhum arquivo encontrado.</div>`;
                return;
            }

            data.files.forEach(file => {
                const div = document.createElement("div");
                div.className = "file";
                div.innerHTML = `
                    <strong>${escapeHtml(file.path)}</strong>
                    <span>${file.size} bytes</span>
                    <br>
                    <a target="_blank" href="/api/workspace/file?path=${encodeURIComponent(file.path)}">abrir arquivo</a>
                `;
                files.appendChild(div);
            });
        }

        loadFiles();
    </script>
</body>
</html>
    """


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


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "base_dir": str(BASE_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
    }