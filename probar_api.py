import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://127.0.0.1:5000"


def pedir(metodo, ruta, datos=None):
    cuerpo = None if datos is None else json.dumps(datos).encode("utf-8")
    peticion = Request(
        BASE + ruta,
        data=cuerpo,
        method=metodo,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(peticion, timeout=30) as respuesta:
            texto = respuesta.read().decode("utf-8")
            print(metodo, ruta, respuesta.status, texto)
            return json.loads(texto) if texto else None
    except HTTPError as error:
        print("Error:", error.code, error.read().decode("utf-8"))
        raise


if __name__ == "__main__":
    producto = pedir("POST", "/api/productos", {
        "nombre": "Prueba API Streetwear",
        "categoria": "Sneakers",
        "estado": "Disponible",
        "precio": 1299.00,
        "descripcion": "Registro temporal para comprobar la API.",
    })
    ruta = "/api/productos/" + producto["id"]
    pedir("GET", ruta)
    pedir("PUT", ruta, {
        "nombre": "Prueba API Streetwear",
        "categoria": "Sneakers",
        "estado": "Agotado",
        "precio": 1299.00,
        "descripcion": "Registro temporal para comprobar la API.",
    })
    pedir("GET", "/api/productos")
    pedir("DELETE", ruta)
