"""Comprueba MONGO_URI, Atlas y soporte de transacciones sin revelar secretos."""
import os
import sys

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv()
uri = (os.environ.get("MONGO_URI") or "").strip()
if not uri:
    raise SystemExit("ERROR: falta MONGO_URI. Revisa tu archivo .env o las variables de Render.")

cliente = MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)
try:
    respuesta = cliente.admin.command("ping")
    if respuesta.get("ok") != 1:
        raise SystemExit("ERROR: MongoDB respondió, pero el ping no fue correcto.")

    hello = cliente.admin.command("hello")
    es_replica_set = bool(hello.get("setName"))
    es_router = hello.get("msg") == "isdbgrid"
    admite_transacciones = es_replica_set or es_router

    db = cliente["tienda_estilo_moda"]
    colecciones = set(db.list_collection_names())
    cantidad_productos = db["productos"].count_documents({}) if "productos" in colecciones else 0

    print("Conexión MongoDB: CORRECTA")
    print("Base de datos: tienda_estilo_moda")
    print(f"Productos encontrados: {cantidad_productos}")
    print("Soporte de transacciones:", "SÍ" if admite_transacciones else "NO")
    if not admite_transacciones:
        print("ERROR: esta instancia parece standalone. La reserva requiere Atlas o un replica set.")
        sys.exit(2)
    print("La base está lista para las reservas temporales.")
except PyMongoError as error:
    print("No se pudo comprobar MongoDB. Revisa URI, usuario, contraseña y Network Access.")
    print(f"Detalle técnico: {type(error).__name__}")
    sys.exit(1)
finally:
    cliente.close()
