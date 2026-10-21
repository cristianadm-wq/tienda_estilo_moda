"""Prueba CRUD de la API usando una sesión de administrador.

Antes de usar este script:
  1. Inicia Flask.
  2. Define TSM_ADMIN_EMAIL y TSM_ADMIN_PASSWORD en la terminal.
  3. No escribas contraseñas dentro de este archivo ni lo subas con secretos.
"""
import json
import os
import re
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor

BASE = os.environ.get("TSM_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
EMAIL = os.environ.get("TSM_ADMIN_EMAIL", "")
PASSWORD = os.environ.get("TSM_ADMIN_PASSWORD", "")
opener = build_opener(HTTPCookieProcessor(CookieJar()))


def enviar(metodo, ruta, datos=None, form=False):
    cuerpo = None if datos is None else (urlencode(datos).encode("utf-8") if form else json.dumps(datos).encode("utf-8"))
    headers = {} if form else {"Content-Type": "application/json"}
    peticion = Request(BASE + ruta, data=cuerpo, method=metodo, headers=headers)
    try:
        with opener.open(peticion, timeout=30) as respuesta:
            texto = respuesta.read().decode("utf-8")
            print(metodo, ruta, respuesta.status, texto[:500])
            return json.loads(texto) if texto and not form else texto
    except HTTPError as error:
        print("Error:", error.code, error.read().decode("utf-8"))
        raise


def iniciar_sesion_admin():
    if not EMAIL or not PASSWORD:
        raise SystemExit("Define TSM_ADMIN_EMAIL y TSM_ADMIN_PASSWORD en esta terminal antes de probar la API.")
    with opener.open(BASE + "/login", timeout=30) as respuesta:
        html = respuesta.read().decode("utf-8")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"\s+name="csrf_token"', html)
    if not match:
        raise SystemExit("No se encontró el token CSRF en /login. Comprueba que la aplicación nueva está ejecutándose.")
    enviar("POST", "/login", {"csrf_token": match.group(1), "correo": EMAIL, "contrasena": PASSWORD, "next": "/admin"}, form=True)


if __name__ == "__main__":
    iniciar_sesion_admin()
    temporal = enviar("POST", "/api/productos", {
        "nombre": "Prueba API TSM", "categoria": "Sneakers", "estado": "Disponible",
        "precio": 1299.00, "stock": 1, "descripcion": "Registro temporal de prueba API.",
    })
    ruta = "/api/productos/" + temporal["id"]
    enviar("GET", ruta)
    enviar("PUT", ruta, {"nombre": "Prueba API TSM", "categoria": "Sneakers", "estado": "Agotado", "precio": 1299.00, "stock": 0, "descripcion": "Prueba API actualizada."})
    enviar("GET", "/api/productos")
    enviar("DELETE", ruta)
