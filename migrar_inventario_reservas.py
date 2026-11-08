"""Inicializa los contadores de reservas sin cambiar las existencias ya registradas.

Ejecutar una sola vez desde la carpeta del proyecto:
    python migrar_inventario_reservas.py

Para productos antiguos sin campo `stock`, usa 10 solo como valor temporal de
prueba cuando el estado sea Disponible. Sustituye ese número por el inventario
real desde el panel administrativo antes de publicar.
"""
from __future__ import annotations

import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    raise SystemExit("Falta MONGO_URI. Revisa el archivo .env.")

cliente = MongoClient(
    mongo_uri,
    serverSelectionTimeoutMS=10000,
    tz_aware=True,
)
productos = cliente["tienda_estilo_moda"]["productos"]

actualizados = 0
simples = 0
variantes_actualizadas = 0

try:
    for producto in productos.find({}):
        cambios = {}
        variantes = producto.get("variantes") or []

        if isinstance(variantes, list) and variantes:
            nuevas_variantes = []
            cambio_variantes = False
            for variante in variantes:
                if not isinstance(variante, dict):
                    nuevas_variantes.append(variante)
                    continue
                copia = dict(variante)
                if "reservadas" not in copia:
                    copia["reservadas"] = 0
                    cambio_variantes = True
                nuevas_variantes.append(copia)
            if cambio_variantes:
                cambios["variantes"] = nuevas_variantes
                variantes_actualizadas += 1
        else:
            if "stock" not in producto:
                # Mismo valor de prueba que contempla la guía para productos viejos.
                cambios["stock"] = 10 if producto.get("estado") == "Disponible" else 0
                simples += 1
            if "reservadas" not in producto:
                cambios["reservadas"] = 0

        if cambios:
            productos.update_one({"_id": producto["_id"]}, {"$set": cambios})
            actualizados += 1

    print(f"Productos revisados: {productos.count_documents({})}")
    print(f"Documentos actualizados: {actualizados}")
    print(f"Productos simples con stock legacy inicializado: {simples}")
    print(f"Productos con variantes actualizadas: {variantes_actualizadas}")
    print("Migración terminada. Verifica y corrige el stock de prueba desde Administración.")
finally:
    cliente.close()
