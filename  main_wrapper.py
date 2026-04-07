# main_wrapper.py
"""
Wrapper que ejecuta main.py y captura el resultado para la API
Este archivo reemplaza la funcionalidad de create_account para retornar datos
"""

import os
import sys
import json
import time
import io
import contextlib

# Redirigir output para capturarlo
from main import create_account, HEROSMS_PHONE, HEROSMS_ACTIVATION_ID

def run_and_capture():
    """Ejecuta create_account y captura el resultado"""
    
    result = {
        "success": False,
        "phone": None,
        "password": None,
        "name": None,
        "cookies": None,
        "error": None
    }
    
    # Capturar output
    output_buffer = io.StringIO()
    
    try:
        with contextlib.redirect_stdout(output_buffer):
            with contextlib.redirect_stderr(output_buffer):
                create_account()
        
        output = output_buffer.getvalue()
        result["output"] = output
        
        # Extraer información del output
        for line in output.split('\n'):
            if "Phone:" in line:
                result["phone"] = line.split(':')[1].strip() if ':' in line else None
            elif "Password:" in line:
                result["password"] = line.split(':')[1].strip() if ':' in line else None
            elif "Name:" in line:
                result["name"] = line.split(':')[1].strip() if ':' in line else None
            elif "CUENTA CREADA EXITOSAMENTE" in line:
                result["success"] = True
        
        # Intentar leer cookies del archivo
        if os.path.exists("cookies.txt"):
            with open("cookies.txt", "r") as f:
                cookies_content = f.read()
                result["cookies_file"] = cookies_content
        
        return result
        
    except Exception as e:
        result["error"] = str(e)
        result["output"] = output_buffer.getvalue()
        return result

if __name__ == "__main__":
    result = run_and_capture()
    print(json.dumps(result, indent=2))
    
    # Guardar resultado en archivo para la API
    with open(f"result_{int(time.time())}.json", "w") as f:
        json.dump(result, f)