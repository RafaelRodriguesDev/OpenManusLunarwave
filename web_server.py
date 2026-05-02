import asyncio
import os
import uuid
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel


app = FastAPI(title="OpenManusWeb", version="0.1.0")

jobs: Dict[str, Dict[str, Any]] = {}
subscribers: Dict[str, List[WebSocket]] = {}

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/workspace")


class RunRequest(BaseModel):
    prompt: str


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def create_job(prompt: str) -> Dict[str, Any]:
    job_id = str(uuid.uuid4())

    job = {
        "id": job_id,
        "prompt": prompt,
        "status": "queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "steps": [
            {"name": "Receber tarefa", "status": "done"},
            {"name": "Carregar agente", "status": "pending"},
            {"name": "Executar raciocínio", "status": "pending"},
            {"name": "Gerar resultado", "status": "pending"},
            {"name": "Finalizar", "status": "pending"},
        ],
        "logs": ["Job criado."],
        "result": None,
        "error": None,
    }

    jobs[job_id] = job
    subscribers[job_id] = []

    return job


async def notify_job(job_id: str) -> None:
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
    job["logs"].append(message)
    job["updated_at"] = now_iso()
    await notify_job(job_id)


async def set_step(job_id: str, step_name: str, status: str) -> None:
    job = jobs[job_id]

    for step in job["steps"]:
        if step["name"] == step_name:
            step["status"] = status

    job["updated_at"] = now_iso()
    await notify_job(job_id)


async def set_status(job_id: str, status: str) -> None:
    jobs[job_id]["status"] = status
    jobs[job_id]["updated_at"] = now_iso()
    await notify_job(job_id)


async def run_openmanus_job(job_id: str, prompt: str) -> None:
    try:
        await set_status(job_id, "running")

        await set_step(job_id, "Carregar agente", "running")
        await add_log(job_id, "Carregando agente OpenManus...")

        from app.agent.manus import Manus

        agent = Manus()

        await set_step(job_id, "Carregar agente", "done")
        await set_step(job_id, "Executar raciocínio", "running")
        await add_log(job_id, "Agente carregado com sucesso.")
        await add_log(job_id, "Executando tarefa...")

        result = await agent.run(prompt)

        await set_step(job_id, "Executar raciocínio", "done")
        await set_step(job_id, "Gerar resultado", "running")

        jobs[job_id]["result"] = str(result) if result is not None else "Tarefa concluída."
        jobs[job_id]["updated_at"] = now_iso()

        await add_log(job_id, "Resultado gerado.")
        await set_step(job_id, "Gerar resultado", "done")
        await set_step(job_id, "Finalizar", "done")
        await set_status(job_id, "completed")
        await add_log(job_id, "Execução finalizada.")

    except Exception as ex:
        error_text = str(ex)
        trace = traceback.format_exc()

        jobs[job_id]["error"] = error_text
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["updated_at"] = now_iso()

        await add_log(job_id, f"Erro: {error_text}")
        await add_log(job_id, trace)

        await set_status(job_id, "failed")


def list_workspace_files() -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []

    if not os.path.exists(WORKSPACE_DIR):
        return files

    for root, _, filenames in os.walk(WORKSPACE_DIR):
        for filename in filenames:
            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, WORKSPACE_DIR)

            try:
                stat = os.stat(full_path)
                files.append({
                    "name": filename,
                    "path": relative_path,
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
    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background: #070b14;
            color: #e5e7eb;
            font-family: Inter, Arial, sans-serif;
        }

        .app {
            height: 100vh;
            display: grid;
            grid-template-columns: 360px 1fr;
            overflow: hidden;
        }

        .sidebar {
            border-right: 1px solid #1f2937;
            background: #0b1020;
            display: flex;
            flex-direction: column;
        }

        .brand {
            padding: 22px;
            border-bottom: 1px solid #1f2937;
        }

        .brand h1 {
            margin: 0;
            font-size: 22px;
            color: #f9fafb;
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
        }

        .bubble.system {
            background: #111827;
            border: 1px solid #1f2937;
            color: #cbd5e1;
        }

        .bubble.user {
            background: #1d4ed8;
            color: white;
        }

        .composer {
            padding: 18px;
            border-top: 1px solid #1f2937;
            background: #0b1020;
        }

        textarea {
            width: 100%;
            height: 120px;
            resize: none;
            background: #020617;
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 14px;
            padding: 14px;
            outline: none;
            font-size: 14px;
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
            grid-template-rows: 70px 1fr 260px;
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
            font-weight: 700;
        }

        .status-pill {
            padding: 8px 12px;
            border-radius: 999px;
            background: #111827;
            border: 1px solid #334155;
            color: #93c5fd;
            font-size: 13px;
        }

        .workspace {
            display: grid;
            grid-template-columns: 1fr 380px;
            overflow: hidden;
        }

        .panel {
            padding: 22px;
            overflow: auto;
            border-right: 1px solid #1f2937;
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
            padding: 16px;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .dot {
            width: 13px;
            height: 13px;
            border-radius: 999px;
            background: #64748b;
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
            font-weight: 700;
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
            min-height: 280px;
            padding: 16px;
            white-space: pre-wrap;
            line-height: 1.5;
            color: #dbeafe;
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
        }

        .file span {
            color: #94a3b8;
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
    </style>
</head>
<body>
    <div class="app">
        <aside class="sidebar">
            <div class="brand">
                <h1>OpenManusWeb</h1>
                <p>Agente na VPS com execução visual básica</p>
            </div>

            <div class="chat" id="chat">
                <div class="bubble system">
                    Pronto. Envie uma tarefa para o agente executar.
                </div>
            </div>

            <div class="composer">
                <textarea id="prompt" placeholder="Ex: Crie uma landing page simples, pesquise um tema, analise um projeto..."></textarea>
                <button id="runBtn" onclick="runTask()">Executar tarefa</button>
            </div>
        </aside>

        <main class="main">
            <header class="topbar">
                <div class="topbar-title">Painel de execução</div>
                <div class="status-pill">Status: <span id="status">aguardando</span></div>
            </header>

            <section class="workspace">
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
                            <div class="step-name">${step.name}</div>
                            <div class="step-status">${step.status}</div>
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
                    <strong>${file.path}</strong>
                    <span>${file.size} bytes</span>
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

    asyncio.create_task(run_openmanus_job(job["id"], prompt))

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
            await websocket.receive_text()

    except WebSocketDisconnect:
        try:
            subscribers[job_id].remove(websocket)
        except Exception:
            pass


@app.get("/api/workspace/files")
async def workspace_files() -> Dict[str, Any]:
    return {
        "workspace": WORKSPACE_DIR,
        "files": list_workspace_files(),
    }


@app.get("/health")
async def health() -> Dict[str, str]:
    return {
        "status": "ok",
    }