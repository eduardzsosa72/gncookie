
import os
import sys
import json
import time
import asyncio
import subprocess
import tempfile
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Amazon Account Creator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Almacenamiento
tasks = {}
results_dir = "results"

os.makedirs(results_dir, exist_ok=True)

class CreateRequest(BaseModel):
    webhook_url: Optional[str] = None
    callback_id: Optional[str] = None
    save_cookies: bool = True

def run_account_creation(task_id: str, webhook_url: Optional[str] = None, 
                         callback_id: Optional[str] = None, save_cookies: bool = True):
    """Ejecuta la creación de cuenta"""
    
    tasks[task_id]["status"] = "running"
    tasks[task_id]["started_at"] = time.time()
    
    # Archivo para el resultado
    result_file = os.path.join(results_dir, f"{task_id}_result.json")
    output_file = os.path.join(results_dir, f"{task_id}_output.txt")
    
    try:
        # Ejecutar main.py
        with open(output_file, 'w') as out_f:
            process = subprocess.Popen(
                [sys.executable, "main.py"],
                stdout=out_f,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                text=True
            )
            
            # Esperar hasta 10 minutos
            return_code = process.wait(timeout=600)
        
        # Leer output
        with open(output_file, 'r') as f:
            output = f.read()
        
        # Extraer resultado
        result = extract_result(output)
        result["output"] = output
        
        # Guardar resultado
        with open(result_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        # Buscar cookies
        cookies = None
        if save_cookies and os.path.exists("cookies.txt"):
            with open("cookies.txt", 'r') as f:
                cookies = f.read()
            result["cookies"] = cookies
        
        tasks[task_id]["status"] = "completed" if result.get("success") else "failed"
        tasks[task_id]["result"] = result
        tasks[task_id]["finished_at"] = time.time()
        
        # Enviar webhook
        if webhook_url:
            send_webhook(webhook_url, task_id, tasks[task_id], callback_id)
            
    except subprocess.TimeoutExpired:
        process.kill()
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = "Process timeout after 10 minutes"
        tasks[task_id]["finished_at"] = time.time()
        
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        tasks[task_id]["finished_at"] = time.time()

def extract_result(output: str) -> Dict:
    """Extrae información del output"""
    result = {
        "success": False,
        "phone": None,
        "password": None,
        "name": None,
        "error_message": None
    }
    
    lines = output.split('\n')
    
    for i, line in enumerate(lines):
        line_lower = line.lower()
        
        if "phone:" in line_lower:
            parts = line.split(':')
            if len(parts) >= 2:
                result["phone"] = parts[1].strip()
        
        elif "password:" in line_lower:
            parts = line.split(':')
            if len(parts) >= 2:
                result["password"] = parts[1].strip()
        
        elif "name:" in line_lower:
            parts = line.split(':')
            if len(parts) >= 2:
                result["name"] = parts[1].strip()
        
        elif "cuenta creada exitosamente" in line_lower:
            result["success"] = True
        
        elif "error" in line_lower and "traceback" in line_lower:
            # Capturar mensaje de error
            error_lines = []
            for j in range(i, min(i+10, len(lines))):
                if lines[j].strip():
                    error_lines.append(lines[j])
            result["error_message"] = '\n'.join(error_lines)
    
    return result

def send_webhook(webhook_url: str, task_id: str, task_data: Dict, callback_id: Optional[str] = None):
    """Envía webhook con resultado"""
    import requests
    
    data = {
        "task_id": task_id,
        "callback_id": callback_id,
        "status": task_data["status"],
        "result": task_data.get("result"),
        "error": task_data.get("error"),
        "timestamp": time.time()
    }
    
    try:
        response = requests.post(webhook_url, json=data, timeout=10)
        print(f"Webhook sent: {response.status_code}")
    except Exception as e:
        print(f"Webhook error: {e}")

@app.get("/")
def root():
    return {
        "service": "Amazon Account Creator",
        "endpoints": {
            "POST /create": "Crear cuenta",
            "GET /status/{task_id}": "Estado",
            "GET /result/{task_id}": "Resultado JSON",
            "GET /output/{task_id}": "Output completo",
            "GET /cookies/{task_id}": "Cookies (si existen)",
            "DELETE /task/{task_id}": "Eliminar tarea"
        }
    }

@app.post("/create")
async def create_account(request: CreateRequest, background_tasks: BackgroundTasks):
    import uuid
    task_id = str(uuid.uuid4())
    
    tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None
    }
    
    background_tasks.add_task(
        run_account_creation,
        task_id,
        request.webhook_url,
        request.callback_id,
        request.save_cookies
    )
    
    return {
        "task_id": task_id,
        "status": "pending",
        "message": "Account creation started"
    }

@app.get("/status/{task_id}")
def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    return tasks[task_id]

@app.get("/result/{task_id}")
def get_result(task_id: str):
    result_file = os.path.join(results_dir, f"{task_id}_result.json")
    
    if os.path.exists(result_file):
        with open(result_file, 'r') as f:
            return json.load(f)
    
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    return tasks[task_id].get("result", {})

@app.get("/output/{task_id}")
def get_output(task_id: str):
    output_file = os.path.join(results_dir, f"{task_id}_output.txt")
    
    if os.path.exists(output_file):
        return FileResponse(output_file, media_type="text/plain")
    
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    return {"output": tasks[task_id].get("result", {}).get("output", "No output available")}

@app.get("/cookies/{task_id}")
def get_cookies(task_id: str):
    cookies_file = "cookies.txt"
    
    if os.path.exists(cookies_file):
        return FileResponse(cookies_file, media_type="text/plain")
    
    raise HTTPException(404, "Cookies file not found")

@app.delete("/task/{task_id}")
def delete_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    # Eliminar archivos asociados
    for ext in ['_result.json', '_output.txt']:
        filepath = os.path.join(results_dir, f"{task_id}{ext}")
        if os.path.exists(filepath):
            os.unlink(filepath)
    
    del tasks[task_id]
    return {"message": "Task deleted"}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "tasks": len(tasks),
        "timestamp": time.time()
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)