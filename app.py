import os
import uuid
import re
import secrets
from io import BytesIO
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from hmac import compare_digest
from urllib.parse import urlparse

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash
import qrcode

load_dotenv()

app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura SECRET_KEY en Render para proteger las sesiones.")
    secret_key = secrets.token_hex(32)
app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
)

mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb://127.0.0.1:27017/"
cliente = MongoClient(
    mongo_uri,
    serverSelectionTimeoutMS=10000,
    tz_aware=True
)
base_datos = cliente["tienda_estilo_moda"]
productos = base_datos["productos"]
pedidos = base_datos["pedidos"]
usuarios = base_datos["usuarios"]
cupones = base_datos["cupones"]
resenas = base_datos["resenas"]
reservas = base_datos["reservas"]
alumnos = productos  # Alias para conservar compatibilidad con la práctica CRUD.

ESTADOS_VALIDOS = ("Disponible", "Agotado")
ESTADOS_PEDIDO = ("Recibido", "Preparando", "Enviado", "Entregado", "Cancelado")
METODOS_PAGO = {
    "tarjeta_demo": "Tarjeta de demostración (sin introducir datos)",
    "transferencia_demo": "Transferencia simulada",
    "contra_entrega_demo": "Pago contra entrega (demostración)",
}
CARRITO_MAX_CANTIDAD = 10
TIEMPO_RESERVA_MINUTOS = 5

# La colección conserva el historial de reservas. Solo puede existir una reserva
# temporal activa por visitante, producto y variante; las canceladas, vencidas y
# confirmadas permanecen como historial y no bloquean una reserva posterior.
clave_reserva = (
    ("reserva_id", 1),
    ("producto_id", 1),
    ("talla", 1),
    ("color", 1),
)
nombre_indice_reserva = "reserva_temporal_carrito_producto_variante_unique"
indice_objetivo_presente = False

if "reservas" in base_datos.list_collection_names(filter={"name": "reservas"}):
    for indice in list(reservas.list_indexes()):
        claves = tuple(indice.get("key", {}).items())
        if claves != clave_reserva:
            continue
        filtro_parcial = indice.get("partialFilterExpression") or {}
        es_indice_objetivo = (
            indice.get("name") == nombre_indice_reserva
            and indice.get("unique") is True
            and filtro_parcial == {"estado": "temporal"}
        )
        if es_indice_objetivo:
            indice_objetivo_presente = True
        else:
            # Retira índices únicos anteriores que impedían conservar el historial.
            reservas.drop_index(indice["name"])

    # Compatibilidad con la versión previa de esta mejora, que usaba "activa".
    reservas.update_many({"estado": "activa"}, {"$set": {"estado": "temporal"}})
    reservas.update_many(
        {"visitante_id": {"$exists": False}},
        [{"$set": {"visitante_id": {"$ifNull": ["$reserva_id", ""]}}}],
    )
    reservas.update_many(
        {"recurso_id": {"$exists": False}},
        [{"$set": {"recurso_id": "$producto_id"}}],
    )
    reservas.update_many({"talla": {"$exists": False}}, {"$set": {"talla": ""}})
    reservas.update_many({"color": {"$exists": False}}, {"$set": {"color": ""}})

if not indice_objetivo_presente:
    reservas.create_index(
        list(clave_reserva),
        unique=True,
        name=nombre_indice_reserva,
        partialFilterExpression={"estado": "temporal"},
    )
reservas.create_index("vence_en")
reservas.create_index([("producto_id", 1), ("estado", 1)], name="reservas_por_producto_estado")

def ahora_utc():
    return datetime.now(timezone.utc)


def obtener_reserva_id():
    if not session.get("reserva_id"):
        session["reserva_id"] = uuid.uuid4().hex
    return session["reserva_id"]


def fecha_utc(valor):
    """Convierte datetime o texto ISO a fecha UTC; devuelve None si no es válido."""
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)
    if isinstance(valor, str) and valor.strip():
        try:
            parsed = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def precio_seguro(valor, predeterminado=0.0):
    try:
        cantidad = Decimal(str(valor))
        if not cantidad.is_finite() or cantidad < 0:
            return predeterminado
        return float(cantidad.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return predeterminado


def entero_seguro(valor, predeterminado=0):
    try:
        n = int(valor)
        return max(0, n)
    except (TypeError, ValueError, OverflowError):
        return predeterminado


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


@app.before_request
def proteger_formularios():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.path.startswith("/api/"):
        enviado = request.form.get("csrf_token", "")
        esperado = session.get("_csrf_token", "")
        if not esperado or not enviado or not compare_digest(enviado, esperado):
            abort(400, description="El formulario expiró o no pasó la verificación de seguridad. Recarga la página e inténtalo de nuevo.")


def usuario_actual():
    if not hasattr(g, "usuario_actual"):
        g.usuario_actual = None
        id_usuario = session.get("usuario_id")
        if id_usuario and ObjectId.is_valid(str(id_usuario)):
            try:
                g.usuario_actual = usuarios.find_one({"_id": ObjectId(str(id_usuario))})
            except PyMongoError:
                return None
            if g.usuario_actual is None:
                session.pop("usuario_id", None)
    return g.usuario_actual


def es_administrador(usuario=None):
    usuario = usuario if usuario is not None else usuario_actual()
    return bool(usuario and usuario.get("rol") == "admin")


def requiere_login(funcion):
    @wraps(funcion)
    def envoltura(*args, **kwargs):
        if not usuario_actual():
            flash("Inicia sesión para continuar.", "info")
            return redirect(url_for("login", next=request.path))
        return funcion(*args, **kwargs)
    return envoltura


def requiere_admin(funcion):
    @wraps(funcion)
    def envoltura(*args, **kwargs):
        usuario = usuario_actual()
        if not usuario:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Debes iniciar sesión como administrador."}), 401
            flash("Inicia sesión con una cuenta administradora para abrir esta sección.", "warning")
            return redirect(url_for("login", next=request.path))
        if not es_administrador(usuario):
            if request.path.startswith("/api/"):
                return jsonify({"error": "No tienes permisos para modificar el inventario."}), 403
            flash("Esta sección solo está disponible para la cuenta administradora.", "danger")
            return redirect(url_for("tienda"))
        return funcion(*args, **kwargs)
    return envoltura


def redireccion_interna_o_tienda(destino):
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return redirect(destino)
    return redirect(url_for("inicio") if es_administrador() else url_for("tienda"))

def stock_de_producto(producto):
    """Existencias físicas totales, sin descontar reservas temporales."""
    variantes = producto.get("variantes") or []
    if isinstance(variantes, list) and variantes:
        return sum(
            entero_seguro(v.get("stock", 0))
            for v in variantes
            if isinstance(v, dict)
        )
    if "stock" in producto:
        return entero_seguro(producto.get("stock", 0))
    # Compatibilidad con productos antiguos que aún no tenían campo stock.
    return 10 if producto.get("estado") == "Disponible" else 0


def stock_disponible_de_producto(producto):
    """Unidades que puede reservar otro visitante ahora mismo."""
    variantes = producto.get("variantes") or []
    if isinstance(variantes, list) and variantes:
        return sum(
            max(
                entero_seguro(v.get("stock", 0))
                - entero_seguro(v.get("reservadas", 0)),
                0,
            )
            for v in variantes
            if isinstance(v, dict)
        )
    return max(
        stock_de_producto(producto)
        - entero_seguro(producto.get("reservadas", 0)),
        0,
    )


def normalizar_producto(producto):
    """Prepara los datos para las vistas sin modificar el documento original."""
    resultado = dict(producto)
    resultado.setdefault("categoria", "Sin categoría")
    resultado.setdefault("estado", "Agotado")
    resultado["precio"] = precio_seguro(resultado.get("precio", 0))
    resultado["precio_original"] = precio_seguro(resultado.get("precio_original", 0))
    resultado.setdefault("descripcion", "Prenda o accesorio streetwear.")
    resultado.setdefault("imagen", "")

    variantes = resultado.get("variantes", [])
    if not isinstance(variantes, list):
        variantes = []

    variantes_limpias = []
    for variante in variantes:
        if not isinstance(variante, dict):
            continue
        stock = entero_seguro(variante.get("stock", 0))
        reservadas = entero_seguro(variante.get("reservadas", 0))
        variantes_limpias.append({
            "talla": str(variante.get("talla", "Única")),
            "color": str(variante.get("color", "Único")),
            "stock": stock,
            "reservadas": reservadas,
            "disponibles": max(stock - reservadas, 0),
        })

    resultado["variantes"] = variantes_limpias
    resultado["stock"] = stock_de_producto(resultado)
    resultado["reservadas"] = (
        sum(v["reservadas"] for v in variantes_limpias)
        if variantes_limpias
        else entero_seguro(resultado.get("reservadas", 0))
    )
    resultado["stock_disponible"] = stock_disponible_de_producto(resultado)

    # Agotado depende del inventario físico, no de una reserva temporal.
    resultado["estado"] = "Disponible" if resultado["stock"] > 0 else "Agotado"

    creado = fecha_utc(resultado.get("creado_en")) or datetime(
        2000, 1, 1, tzinfo=timezone.utc
    )
    drop_at = fecha_utc(resultado.get("drop_at"))
    resultado["creado_en"] = creado
    resultado["drop_at_dt"] = drop_at
    resultado["drop_at_iso"] = drop_at.isoformat() if drop_at else ""
    resultado["drop_pendiente"] = bool(drop_at and drop_at > ahora_utc())
    resultado["es_nuevo"] = creado >= ahora_utc() - timedelta(days=14)
    resultado["en_oferta"] = bool(
        resultado["precio_original"] > resultado["precio"] > 0
    )
    resultado["porcentaje_descuento"] = (
        int(round((1 - resultado["precio"] / resultado["precio_original"]) * 100))
        if resultado["en_oferta"]
        else 0
    )
    return resultado


def inventario_variante(producto, talla="", color=""):
    """Devuelve las unidades libres de una variante o del producto simple."""
    variantes = producto.get("variantes") or []
    if variantes:
        for variante in variantes:
            if (
                str(variante.get("talla", "")) == str(talla)
                and str(variante.get("color", "")) == str(color)
            ):
                return max(
                    entero_seguro(variante.get("stock", 0))
                    - entero_seguro(variante.get("reservadas", 0)),
                    0,
                )
        return 0
    return max(
        stock_de_producto(producto)
        - entero_seguro(producto.get("reservadas", 0)),
        0,
    )


def asegurar_stock_legacy(producto):
    """Inicializa campos faltantes de documentos antiguos sin pisar reservas."""
    if not producto:
        return producto
    if producto.get("variantes"):
        return producto

    cambios = {}
    condiciones = {"_id": producto["_id"]}
    if "stock" not in producto:
        cambios["stock"] = 10 if producto.get("estado") == "Disponible" else 0
        condiciones["stock"] = {"$exists": False}
    if "reservadas" not in producto:
        cambios["reservadas"] = 0
        condiciones["reservadas"] = {"$exists": False}
    if cambios:
        productos.update_one(condiciones, {"$set": cambios})
        # Releer evita sobrescribir una reserva que haya ocurrido en paralelo.
        actualizado = productos.find_one({"_id": producto["_id"]})
        return actualizado or producto
    return producto


def _incrementar_reservadas_producto(producto, cantidad, talla, color, mongo_session):
    """Aparta stock con un filtro atómico: nunca permite reservas negativas."""
    pid = producto["_id"]
    variantes = producto.get("variantes") or []
    if variantes:
        filtro = {
            "_id": pid,
            "estado": "Disponible",
            "$expr": {
                "$gt": [
                    {
                        "$size": {
                            "$filter": {
                                "input": {"$ifNull": ["$variantes", []]},
                                "as": "v",
                                "cond": {
                                    "$and": [
                                        {"$eq": ["$$v.talla", talla]},
                                        {"$eq": ["$$v.color", color]},
                                        {
                                            "$gte": [
                                                {
                                                    "$subtract": [
                                                        {"$ifNull": ["$$v.stock", 0]},
                                                        {"$ifNull": ["$$v.reservadas", 0]},
                                                    ]
                                                },
                                                cantidad,
                                            ]
                                        },
                                    ]
                                },
                            }
                        }
                    },
                    0,
                ]
            },
        }
        resultado = productos.update_one(
            filtro,
            {"$inc": {"variantes.$[v].reservadas": cantidad}},
            array_filters=[{"v.talla": talla, "v.color": color}],
            session=mongo_session,
        )
    else:
        filtro = {
            "_id": pid,
            "estado": "Disponible",
            "$expr": {
                "$gte": [
                    {
                        "$subtract": [
                            {"$ifNull": ["$stock", 0]},
                            {"$ifNull": ["$reservadas", 0]},
                        ]
                    },
                    cantidad,
                ]
            },
        }
        resultado = productos.update_one(
            filtro,
            {"$inc": {"reservadas": cantidad}},
            session=mongo_session,
        )
    return resultado.modified_count == 1


def _restar_reservadas_producto(producto, cantidad, talla, color, mongo_session):
    """Devuelve al inventario unidades que estaban apartadas."""
    if cantidad <= 0:
        return True
    pid = producto["_id"]
    variantes = producto.get("variantes") or []
    if variantes:
        filtro = {
            "_id": pid,
            "variantes": {
                "$elemMatch": {
                    "talla": talla,
                    "color": color,
                    "reservadas": {"$gte": cantidad},
                }
            },
        }
        resultado = productos.update_one(
            filtro,
            {"$inc": {"variantes.$[v].reservadas": -cantidad}},
            array_filters=[
                {"v.talla": talla, "v.color": color, "v.reservadas": {"$gte": cantidad}}
            ],
            session=mongo_session,
        )
    else:
        resultado = productos.update_one(
            {"_id": pid, "reservadas": {"$gte": cantidad}},
            {"$inc": {"reservadas": -cantidad}},
            session=mongo_session,
        )
    return resultado.modified_count == 1


def reservar_cantidad(producto_id, reserva_id, cantidad=1, talla="", color=""):
    """Reserva una cantidad en Atlas y registra su vencimiento en una transacción.

    Reintenta conflictos transitorios para que dos visitantes que compiten por la
    última unidad obtengan un resultado normal: uno reserva y el otro es rechazado.
    """
    if not ObjectId.is_valid(str(producto_id)):
        return False
    cantidad = entero_seguro(cantidad, 0)
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        return False

    oid = ObjectId(str(producto_id))
    producto = productos.find_one({"_id": oid})
    if not producto:
        return False
    producto = asegurar_stock_legacy(producto)
    variantes = producto.get("variantes") or []
    if variantes and not any(
        str(v.get("talla", "")) == str(talla)
        and str(v.get("color", "")) == str(color)
        for v in variantes
    ):
        return False

    for intento in range(3):
        ahora = ahora_utc()
        vence = ahora + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)
        try:
            with cliente.start_session() as mongo_session:
                with mongo_session.start_transaction():
                    existente = reservas.find_one(
                        {
                            "reserva_id": reserva_id,
                            "producto_id": oid,
                            "talla": str(talla),
                            "color": str(color),
                            "estado": "temporal",
                            "vence_en": {"$gt": ahora},
                        },
                        session=mongo_session,
                    )
                    if existente:
                        cantidad_actual = entero_seguro(existente.get("cantidad", 0))
                    else:
                        cantidad_actual = 0
                    if cantidad_actual + cantidad > CARRITO_MAX_CANTIDAD:
                        return False

                    producto_tx = productos.find_one({"_id": oid}, session=mongo_session)
                    if not producto_tx:
                        return False
                    if not _incrementar_reservadas_producto(
                        producto_tx, cantidad, talla, color, mongo_session
                    ):
                        return False

                    reservas.update_one(
                        {
                            "reserva_id": reserva_id,
                            "producto_id": oid,
                            "talla": str(talla),
                            "color": str(color),
                            "estado": "temporal",
                        },
                        {
                            "$inc": {"cantidad": cantidad},
                            "$set": {
                                "visitante_id": reserva_id,
                                "recurso_id": oid,
                                "talla": str(talla),
                                "color": str(color),
                                "estado": "temporal",
                                "vence_en": vence,
                            },
                            "$setOnInsert": {"creada_en": ahora},
                        },
                        upsert=True,
                        session=mongo_session,
                    )
            return True
        except DuplicateKeyError:
            if intento == 2:
                raise
        except PyMongoError as error:
            transitorio = getattr(error, "has_error_label", lambda _label: False)(
                "TransientTransactionError"
            )
            if not transitorio or intento == 2:
                raise
    return False


def reservar_unidad(producto_id, reserva_id, talla="", color=""):
    """Atajo equivalente a un clic de reserva de una unidad."""
    return reservar_cantidad(producto_id, reserva_id, 1, talla, color)


def liberar_reservas_vencidas():
    """Marca vencidas y devuelve existencias mediante transacciones.

    El documento se conserva en Atlas con estado ``vencida`` para el historial.
    """
    ahora = ahora_utc()
    vencidas = list(
        reservas.find({"estado": "temporal", "vence_en": {"$lte": ahora}})
    )
    for candidata in vencidas:
        with cliente.start_session() as mongo_session:
            with mongo_session.start_transaction():
                vencida = reservas.find_one_and_update(
                    {
                        "_id": candidata["_id"],
                        "estado": "temporal",
                        "vence_en": {"$lte": ahora},
                    },
                    {"$set": {"estado": "vencida", "vencida_en": ahora, "liberada_en": ahora}},
                    return_document=ReturnDocument.BEFORE,
                    session=mongo_session,
                )
                if not vencida:
                    continue
                producto = productos.find_one(
                    {"_id": vencida["producto_id"]},
                    session=mongo_session,
                )
                if not producto:
                    continue
                cantidad = entero_seguro(vencida.get("cantidad", 0))
                if not _restar_reservadas_producto(
                    producto,
                    cantidad,
                    str(vencida.get("talla", "")),
                    str(vencida.get("color", "")),
                    mongo_session,
                ):
                    raise RuntimeError("No se pudo liberar el inventario de una reserva vencida.")


def eliminar_reserva(reserva_id, producto_id, talla="", color=""):
    """Cancela una reserva temporal y conserva su registro histórico."""
    if not reserva_id or not ObjectId.is_valid(str(producto_id)):
        return False
    oid = ObjectId(str(producto_id))
    ahora = ahora_utc()
    with cliente.start_session() as mongo_session:
        with mongo_session.start_transaction():
            cancelada = reservas.find_one_and_update(
                {
                    "reserva_id": reserva_id,
                    "producto_id": oid,
                    "talla": str(talla),
                    "color": str(color),
                    "estado": "temporal",
                    "vence_en": {"$gt": ahora},
                },
                {"$set": {"estado": "cancelada", "cancelada_en": ahora, "liberada_en": ahora}},
                return_document=ReturnDocument.BEFORE,
                session=mongo_session,
            )
            if not cancelada:
                return False
            producto = productos.find_one({"_id": oid}, session=mongo_session)
            if producto:
                cantidad = entero_seguro(cancelada.get("cantidad", 0))
                if not _restar_reservadas_producto(
                    producto, cantidad, str(talla), str(color), mongo_session
                ):
                    raise RuntimeError("No se pudo devolver el inventario al cancelar la reserva.")
    return True


def actualizar_reserva_cantidad(reserva_id, producto_id, nueva_cantidad, talla="", color=""):
    """Aumenta o reduce una variante usando solo la diferencia de unidades."""
    nueva_cantidad = entero_seguro(nueva_cantidad, 0)
    if nueva_cantidad < 1 or nueva_cantidad > CARRITO_MAX_CANTIDAD:
        return False
    oid = ObjectId(str(producto_id))
    existente = reservas.find_one({
        "reserva_id": reserva_id,
        "producto_id": oid,
        "talla": str(talla),
        "color": str(color),
        "estado": "temporal",
        "vence_en": {"$gt": ahora_utc()},
    })
    if not existente:
        return False
    anterior = entero_seguro(existente.get("cantidad", 0))
    talla = str(talla)
    color = str(color)
    diferencia = nueva_cantidad - anterior
    if diferencia == 0:
        return True
    if diferencia > 0:
        return reservar_cantidad(oid, reserva_id, diferencia, talla, color)

    liberar = -diferencia
    ahora = ahora_utc()
    with cliente.start_session() as mongo_session:
        with mongo_session.start_transaction():
            actual = reservas.find_one({
                "reserva_id": reserva_id,
                "producto_id": oid,
                "talla": talla,
                "color": color,
                "estado": "temporal",
                "vence_en": {"$gt": ahora},
                "cantidad": {"$gte": liberar},
            }, session=mongo_session)
            if not actual:
                return False
            producto = productos.find_one({"_id": oid}, session=mongo_session)
            if not producto:
                return False
            if not _restar_reservadas_producto(
                producto, liberar, talla, color, mongo_session
            ):
                return False
            actualizado = reservas.update_one(
                {"_id": actual["_id"], "cantidad": anterior},
                {
                    "$set": {
                        "cantidad": nueva_cantidad,
                        "vence_en": ahora + timedelta(minutes=TIEMPO_RESERVA_MINUTOS),
                    }
                },
                session=mongo_session,
            )
            if actualizado.modified_count != 1:
                raise RuntimeError("La reserva cambió mientras se actualizaba la cantidad.")
    return True


def producto_comprable(producto):
    producto = normalizar_producto(producto)

    return (
        producto["estado"] == "Disponible"
        and producto["stock_disponible"] > 0
        and not producto["drop_pendiente"]
        and producto["precio"] > 0
    )


def parsear_variantes(texto):
    variantes = []
    unicas = set()
    for numero, linea in enumerate((texto or "").splitlines(), start=1):
        linea = linea.strip()
        if not linea:
            continue
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) != 3:
            abort(400, description=f"La variante de la línea {numero} debe usar: talla | color | existencias.")
        talla, color, stock_raw = partes
        if not talla or not color or len(talla) > 20 or len(color) > 40:
            abort(400, description=f"Revisa la talla y color en la línea {numero}.")
        try:
            stock = int(stock_raw)
        except ValueError:
            abort(400, description=f"Las existencias de la línea {numero} deben ser un número entero.")
        if stock < 0 or stock > 100000:
            abort(400, description=f"Las existencias de la línea {numero} deben estar entre 0 y 100000.")
        clave = (talla.casefold(), color.casefold())
        if clave in unicas:
            abort(400, description=f"La combinación de talla y color se repite en la línea {numero}.")
        unicas.add(clave)
        variantes.append({"talla": talla, "color": color, "stock": stock, "reservadas": 0})
    if len(variantes) > 100:
        abort(400, description="Puedes registrar hasta 100 combinaciones de talla y color por producto.")
    return variantes


def leer_formulario(datos=None):
    es_formulario_html = datos is None
    if datos is None:
        datos = request.form
    campos_texto = ("nombre", "categoria", "estado")
    if any(not isinstance(datos.get(c, ""), str) for c in campos_texto):
        abort(400, description="Los campos deben contener texto.")
    nombre = datos.get("nombre", "").strip()
    categoria = datos.get("categoria", "").strip()
    estado = datos.get("estado", "").strip()
    if not nombre or not categoria or not estado:
        abort(400, description="Completa nombre, categoría y estado.")
    if len(nombre) > 80 or len(categoria) > 40:
        abort(400, description="Máximo: nombre 80 y categoría 40 caracteres.")
    if estado not in ESTADOS_VALIDOS:
        abort(400, description="Selecciona un estado válido.")

    precio_raw = datos.get("precio", "")
    if es_formulario_html and not str(precio_raw).strip():
        abort(400, description="Indica el precio del producto.")
    try:
        precio_decimal = Decimal(str(precio_raw).strip() or "0")
        if not precio_decimal.is_finite() or precio_decimal < 0:
            abort(400, description="Escribe un precio válido.")
        precio = float(precio_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        abort(400, description="Escribe un precio válido, por ejemplo 1299.00.")

    precio_original_raw = str(datos.get("precio_original", "") or "").strip()
    try:
        precio_original = float(Decimal(precio_original_raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if precio_original_raw else 0.0
    except (InvalidOperation, ValueError):
        abort(400, description="El precio original debe ser un número válido.")
    if precio_original < 0 or precio_original > 100000000:
        abort(400, description="El precio original no es válido.")
    if precio_original <= precio:
        precio_original = 0.0

    descripcion = datos.get("descripcion", "")
    imagen = datos.get("imagen", "")
    if not isinstance(descripcion, str) or not isinstance(imagen, str):
        abort(400, description="La descripción y la imagen deben ser texto.")
    descripcion = descripcion.strip()
    imagen = imagen.strip()
    if len(descripcion) > 1200:
        abort(400, description="La descripción admite hasta 1200 caracteres.")
    if imagen:
        partes_url = urlparse(imagen)
        if partes_url.scheme not in ("https", "http") or not partes_url.netloc:
            abort(400, description="La imagen debe usar una dirección HTTP o HTTPS completa.")

    variantes_texto = datos.get("variantes_texto", "")
    variantes = parsear_variantes(variantes_texto)
    stock_raw = datos.get("stock", "10" if estado == "Disponible" else "0")
    try:
        stock = int(stock_raw)
    except (TypeError, ValueError):
        abort(400, description="Las existencias deben ser un número entero.")
    if stock < 0 or stock > 100000:
        abort(400, description="Las existencias deben estar entre 0 y 100000.")
    if variantes:
        stock = sum(v["stock"] for v in variantes)
    estado_final = "Disponible" if stock > 0 else "Agotado"

    drop_raw = str(datos.get("drop_at", "") or "").strip()
    drop_at = fecha_utc(drop_raw) if drop_raw else None
    if drop_raw and drop_at is None:
        abort(400, description="La fecha del drop no tiene un formato válido.")

    return {
        "nombre": nombre,
        "categoria": categoria,
        "estado": estado_final,
        "precio": precio,
        "precio_original": precio_original,
        "descripcion": descripcion or "Prenda o accesorio streetwear.",
        "imagen": imagen,
        "variantes": variantes,
        "stock": stock,
        "reservadas": 0,
        "drop_at": drop_at,
    }


def buscar_producto(id):
    if not ObjectId.is_valid(str(id)):
        abort(404, description="La clave del producto no es válida.")
    producto = productos.find_one({"_id": ObjectId(str(id))})
    if producto is None:
        abort(404, description="Este producto ya no existe.")
    return normalizar_producto(producto)


# Alias de la función original, por compatibilidad con las rutas CRUD.
buscar_alumno = buscar_producto


def producto_json(producto):
    p = normalizar_producto(producto)
    return {
        "id": str(p["_id"]), "nombre": p["nombre"], "categoria": p["categoria"],
        "estado": p["estado"], "precio": p["precio"], "precio_original": p["precio_original"],
        "descripcion": p["descripcion"], "imagen": p["imagen"],
        "stock": p["stock"], "reservadas": p["reservadas"],
        "stock_disponible": p["stock_disponible"],
        "variantes": p["variantes"], "creado_en": p["creado_en"].isoformat(),
        "drop_at": p["drop_at_iso"], "drop_pendiente": p["drop_pendiente"],
    }


def leer_json():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        abort(400, description="Envía un objeto JSON válido.")
    return leer_formulario(datos)


def cantidad_carrito():
    reserva_id = session.get("reserva_id")
    if not reserva_id:
        return 0
    ahora = ahora_utc()
    items = reservas.find({
        "reserva_id": reserva_id,
        "estado": "temporal",
        "vence_en": {"$gt": ahora},
    })
    return sum(entero_seguro(item.get("cantidad", 0)) for item in items)


def obtener_articulos_carrito():
    """Lee el carrito desde las reservas persistidas en MongoDB Atlas."""
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    if not reserva_id:
        return [], 0.0

    ahora = ahora_utc()
    items = list(reservas.find({
        "reserva_id": reserva_id,
        "estado": "temporal",
        "vence_en": {"$gt": ahora},
    }).sort("vence_en", 1))

    articulos = []
    total = 0.0
    for item in items:
        producto_raw = productos.find_one({"_id": item["producto_id"]})
        if not producto_raw:
            continue
        producto = normalizar_producto(producto_raw)
        cantidad = entero_seguro(item.get("cantidad", 0))
        if cantidad <= 0:
            continue

        talla = str(item.get("talla", ""))
        color = str(item.get("color", ""))
        variantes = producto_raw.get("variantes") or []
        if variantes and not any(
            str(v.get("talla", "")) == talla
            and str(v.get("color", "")) == color
            for v in variantes
        ):
            # Protección ante datos antiguos dañados; no inventa una variante.
            continue

        precio = producto["precio"]
        subtotal = round(precio * cantidad, 2)
        articulos.append({
            **producto,
            "cantidad": cantidad,
            "talla": talla,
            "color": color,
            "linea_id": str(producto["_id"]),
            "subtotal": subtotal,
        })
        total += subtotal

    return articulos, round(total, 2)


def es_cupon_valido(cupon, total, id_usuario=None):
    if not cupon or not cupon.get("activo", True):
        return False, "Ese cupón no existe o está desactivado."
    inicio = fecha_utc(cupon.get("fecha_inicio"))
    fin = fecha_utc(cupon.get("fecha_fin"))
    actual = ahora_utc()
    if inicio and actual < inicio:
        return False, "Ese cupón todavía no está vigente."
    if fin and actual > fin:
        return False, "Ese cupón ya venció."
    minimo = precio_seguro(cupon.get("compra_minima", 0))
    if total < minimo:
        return False, f"La compra mínima para ese cupón es {formato_moneda(minimo)}."
    max_usos = entero_seguro(cupon.get("uso_maximo", 0))
    if max_usos and entero_seguro(cupon.get("usos", 0)) >= max_usos:
        return False, "Ese cupón ya alcanzó su límite de usos."
    if id_usuario and str(id_usuario) in [str(x) for x in cupon.get("usuarios_que_lo_usaron", [])]:
        return False, "Ya utilizaste ese cupón en un pedido anterior."
    return True, "Cupón válido."


def descuento_cupon(total, id_usuario=None):
    codigo = str(session.get("cupon_codigo", "") or "").strip().upper()
    if not codigo:
        return None, 0.0
    cupon = cupones.find_one({"codigo": codigo})
    valido, _ = es_cupon_valido(cupon, total, id_usuario)
    if not valido:
        session.pop("cupon_codigo", None)
        return None, 0.0
    valor = precio_seguro(cupon.get("valor", 0))
    if cupon.get("tipo") == "porcentaje":
        descuento = total * min(valor, 100) / 100
    else:
        descuento = min(valor, total)
    return cupon, round(min(total, max(0, descuento)), 2)


@app.template_filter("moneda")
def formato_moneda(valor):
    return f"${precio_seguro(valor):,.2f} MXN"


@app.template_filter("fecha_corta")
def fecha_corta(valor):
    fecha = fecha_utc(valor)
    return fecha.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC") if fecha else "—"


@app.context_processor
def variables_compartidas():
    usuario = usuario_actual()
    publico = None
    favoritos = []
    if usuario:
        publico = {"nombre": usuario.get("nombre", "Usuario"), "correo": usuario.get("correo", ""), "rol": usuario.get("rol", "cliente")}
        raw = usuario.get("favoritos", [])
        if isinstance(raw, list):
            favoritos = [str(x) for x in raw]
    return {
        "carrito_cantidad": cantidad_carrito(),
        "usuario_actual": publico,
        "usuario_es_admin": es_administrador(usuario),
        "favoritos_ids": favoritos,
        "csrf_token": csrf_token,
        "admin_setup_disponible": bool(os.environ.get("ADMIN_SETUP_KEY")),
    }


# -------------------------- CUENTAS Y AUTENTICACIÓN ------------------------
@app.route("/registro", methods=["GET", "POST"])
def registro():
    if usuario_actual():
        return redirect(url_for("inicio") if es_administrador() else url_for("tienda"))
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        contrasena = request.form.get("contrasena", "")
        confirmar = request.form.get("confirmar_contrasena", "")
        clave_admin = request.form.get("clave_admin", "")
        if not nombre or len(nombre) > 80:
            flash("Escribe tu nombre (máximo 80 caracteres).", "warning")
            return redirect(url_for("registro"))
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", correo) or len(correo) > 160:
            flash("Escribe un correo electrónico válido.", "warning")
            return redirect(url_for("registro"))
        if len(contrasena) < 8 or len(contrasena) > 128:
            flash("La contraseña debe tener entre 8 y 128 caracteres.", "warning")
            return redirect(url_for("registro"))
        if contrasena != confirmar:
            flash("Las contraseñas no coinciden.", "warning")
            return redirect(url_for("registro"))
        rol = "cliente"
        if clave_admin:
            clave_configurada = os.environ.get("ADMIN_SETUP_KEY", "")
            if not clave_configurada or not compare_digest(clave_admin, clave_configurada):
                flash("La clave de administrador no es válida o no está habilitada.", "danger")
                return redirect(url_for("registro"))
            if usuarios.find_one({"rol": "admin"}):
                flash("Ya existe una cuenta administradora. El registro se hará como cliente.", "warning")
                return redirect(url_for("registro"))
            rol = "admin"
        usuarios.create_index("correo", unique=True)
        documento = {"nombre": nombre, "correo": correo, "contrasena_hash": generate_password_hash(contrasena), "rol": rol, "favoritos": [], "creado_en": ahora_utc()}
        try:
            resultado = usuarios.insert_one(documento)
        except DuplicateKeyError:
            flash("Ese correo ya tiene una cuenta. Inicia sesión.", "warning")
            return redirect(url_for("login"))
        reserva_guardada = session.get("reserva_id")
        cupon_guardado = session.get("cupon_codigo")
        session.clear()
        if reserva_guardada:
            session["reserva_id"] = reserva_guardada
        if cupon_guardado:
            session["cupon_codigo"] = cupon_guardado
        session["usuario_id"] = str(resultado.inserted_id)
        session.permanent = True
        g.usuario_actual = {**documento, "_id": resultado.inserted_id}
        flash("Cuenta creada correctamente." + (" Tu cuenta tiene permisos de administrador." if rol == "admin" else ""), "success")
        return redireccion_interna_o_tienda(request.form.get("next", ""))
    return render_template("registro.html", next_url=request.args.get("next", ""))


@app.route("/login", methods=["GET", "POST"])
def login():
    if usuario_actual():
        return redirect(url_for("inicio") if es_administrador() else url_for("tienda"))
    destino = request.args.get("next", "")
    if request.method == "POST":
        correo = request.form.get("correo", "").strip().lower()
        contrasena = request.form.get("contrasena", "")
        usuario = usuarios.find_one({"correo": correo}) if correo else None
        if not usuario or not check_password_hash(usuario.get("contrasena_hash", ""), contrasena):
            flash("El correo o la contraseña no son correctos.", "danger")
            return redirect(url_for("login", next=request.form.get("next", "")))
        reserva_guardada = session.get("reserva_id")
        cupon_guardado = session.get("cupon_codigo")
        session.clear()
        if reserva_guardada:
            session["reserva_id"] = reserva_guardada
        if cupon_guardado:
            session["cupon_codigo"] = cupon_guardado
        session["usuario_id"] = str(usuario["_id"])
        session.permanent = True
        g.usuario_actual = usuario
        flash(f"Bienvenido/a, {usuario.get('nombre', 'usuario')}.", "success")
        return redireccion_interna_o_tienda(request.form.get("next", ""))
    return render_template("login.html", next_url=destino)


@app.route("/salir", methods=["POST"])
def salir():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    if reserva_id:
        abiertas = list(reservas.find({
            "reserva_id": reserva_id,
            "estado": "temporal",
            "vence_en": {"$gt": ahora_utc()},
        }))
        for item in abiertas:
            eliminar_reserva(
                reserva_id,
                str(item["producto_id"]),
                str(item.get("talla", "")),
                str(item.get("color", "")),
            )
    session.clear()
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("tienda"))


# ------------------------------ CATÁLOGO ------------------------------------
def obtener_catalogo_filtrado(args):
    query = {}
    q = str(args.get("q", "")).strip()[:100]
    categoria = str(args.get("categoria", "")).strip()[:40]
    estado = str(args.get("estado", "")).strip()
    minimo_raw = str(args.get("min_precio", "")).strip()
    maximo_raw = str(args.get("max_precio", "")).strip()
    if q:
        regex = re.escape(q)
        query["$or"] = [{campo: {"$regex": regex, "$options": "i"}} for campo in ("nombre", "categoria", "descripcion")]
    if categoria:
        query["categoria"] = categoria
    rango = {}
    try:
        if minimo_raw:
            rango["$gte"] = max(0, float(minimo_raw))
        if maximo_raw:
            rango["$lte"] = max(0, float(maximo_raw))
    except ValueError:
        pass
    if rango:
        query["precio"] = rango
    ordenar = str(args.get("orden", "nombre"))
    ordenar_mapa = {"nombre": [("nombre", 1)], "precio_asc": [("precio", 1)], "precio_desc": [("precio", -1)], "recientes": [("creado_en", -1), ("nombre", 1)]}
    docs = productos.find(query).sort(ordenar_mapa.get(ordenar, ordenar_mapa["nombre"]))
    lista = [normalizar_producto(p) for p in docs]
    if estado in ESTADOS_VALIDOS:
        lista = [p for p in lista if p["estado"] == estado]
    return lista, q, categoria, estado, minimo_raw, maximo_raw, ordenar


@app.route("/")
def tienda():
    liberar_reservas_vencidas()
    lista, q, categoria, estado, minimo, maximo, orden = obtener_catalogo_filtrado(request.args)
    categorias = sorted([c for c in productos.distinct("categoria") if isinstance(c, str) and c.strip()])
    filtros = {"q": q, "categoria": categoria, "estado": estado, "min_precio": minimo, "max_precio": maximo, "orden": orden}
    return render_template("tienda.html", productos=lista, busqueda=q, categorias=categorias, filtros=filtros, next_url=(request.full_path or "/") + "#catalogo")


@app.route("/catalogo/resultados")
def resultados_catalogo():
    liberar_reservas_vencidas()
    lista, q, categoria, estado, minimo, maximo, orden = obtener_catalogo_filtrado(request.args)
    return render_template("_product_grid.html", productos=lista, busqueda=q, next_url=url_for("tienda") + (("?" + request.query_string.decode("utf-8")) if request.query_string else "") + "#catalogo")


@app.route("/salud")
@app.route("/api/salud")
def salud():
    """Verificación sencilla del servicio y su conexión con MongoDB Atlas."""
    cliente.admin.command("ping")
    return jsonify({"ok": True, "mensaje": "Tienda Estilo Moda y MongoDB Atlas funcionan."})


def url_publica_ficha_producto(producto_id):
    """Construye una URL pública estable para el QR (Render mediante APP_BASE_URL)."""
    ruta = url_for("ficha_publica", id=str(producto_id))
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}{ruta}"
    return url_for("ficha_publica", id=str(producto_id), _external=True)


@app.route("/qr/producto/<id>.png")
def qr_producto(id):
    producto = buscar_producto(id)
    url_ficha = url_publica_ficha_producto(producto["_id"])
    imagen = qrcode.make(url_ficha, box_size=8, border=4)
    salida = BytesIO()
    imagen.save(salida, format="PNG")
    salida.seek(0)
    respuesta = send_file(
        salida,
        mimetype="image/png",
        download_name=f"TSM-QR-{producto['_id']}.png",
        max_age=300,
    )
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    return respuesta


@app.route("/ficha/<id>")
def ficha_publica(id):
    liberar_reservas_vencidas()
    producto = buscar_producto(id)
    return render_template(
        "ficha_publica.html",
        producto=producto,
        url_ficha=url_publica_ficha_producto(producto["_id"]),
    )


@app.route("/producto/<id>")
def detalle_producto(id):
    liberar_reservas_vencidas()
    producto = buscar_producto(id)
    relacionados = [normalizar_producto(p) for p in productos.find({"categoria": producto.get("categoria")}).limit(8) if str(p.get("_id")) != str(producto.get("_id"))][:4]
    lista_resenas = list(resenas.find({"producto_id": str(producto["_id"]), "visible": True}).sort("creado_en", -1).limit(30))
    promedio = round(sum(entero_seguro(r.get("calificacion")) for r in lista_resenas) / len(lista_resenas), 1) if lista_resenas else 0
    usuario = usuario_actual()
    puede_opinar = False
    resena_propia = None
    if usuario:
        resena_propia = resenas.find_one({"producto_id": str(producto["_id"]), "usuario_id": str(usuario["_id"])})
        puede_opinar = bool(pedidos.find_one({"usuario_id": str(usuario["_id"]), "items.producto_id": str(producto["_id"])}))
    tallas = sorted({v["talla"] for v in producto["variantes"]}, key=str)
    colores = sorted({v["color"] for v in producto["variantes"]}, key=str)
    return render_template("producto.html", producto=producto, relacionados=relacionados, resenas=lista_resenas, promedio=promedio, puede_opinar=puede_opinar, resena_propia=resena_propia, tallas=tallas, colores=colores)


# -------------------------------- FAVORITOS ---------------------------------
@app.route("/favoritos")
@requiere_login
def ver_favoritos():
    usuario = usuario_actual()
    raw = usuario.get("favoritos", []) if usuario else []
    ids = [ObjectId(str(x)) for x in raw if ObjectId.is_valid(str(x))] if isinstance(raw, list) else []
    lista = [normalizar_producto(p) for p in productos.find({"_id": {"$in": ids}}).sort("nombre", 1)] if ids else []
    categorias = sorted([c for c in productos.distinct("categoria") if isinstance(c, str) and c.strip()])
    return render_template("favoritos.html", productos=lista, categorias=categorias, next_url=url_for("ver_favoritos"))


@app.route("/favoritos/alternar/<id>", methods=["POST"])
@requiere_login
def alternar_favorito(id):
    producto = buscar_producto(id)
    usuario = usuario_actual()
    pid = str(producto["_id"])
    favoritos = [str(x) for x in usuario.get("favoritos", []) if ObjectId.is_valid(str(x))]
    if pid in favoritos:
        usuarios.update_one({"_id": usuario["_id"]}, {"$pull": {"favoritos": pid}})
        flash("Producto eliminado de tus favoritos.", "info")
    else:
        usuarios.update_one({"_id": usuario["_id"]}, {"$addToSet": {"favoritos": pid}})
        flash("Producto agregado a tus favoritos.", "success")
    destino = request.form.get("next", "")
    if destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return redirect(destino)
    return redirect(url_for("detalle_producto", id=pid))


# -------------------------------- CARRITO -----------------------------------
@app.route("/carrito")
def ver_carrito():
    liberar_reservas_vencidas()
    articulos, subtotal = obtener_articulos_carrito()
    reserva_id = session.get("reserva_id")
    items = list(reservas.find({
        "reserva_id": reserva_id,
        "estado": "temporal",
        "vence_en": {"$gt": ahora_utc()},
    })) if reserva_id else []
    fechas = [fecha_utc(item.get("vence_en")) for item in items]
    fechas = [fecha for fecha in fechas if fecha is not None]
    vence_en = min(fechas, default=None)

    usuario = usuario_actual()
    cupon, descuento = descuento_cupon(
        subtotal,
        str(usuario["_id"]) if usuario else None,
    )
    return render_template(
        "carrito.html",
        articulos=articulos,
        total=subtotal,
        subtotal=subtotal,
        cupon=cupon,
        descuento=descuento,
        total_final=round(subtotal - descuento, 2),
        vence_en=vence_en,
    )


@app.route("/carrito/agregar/<id>", methods=["POST"])
def agregar_carrito(id):
    liberar_reservas_vencidas()
    if not ObjectId.is_valid(id):
        abort(404)

    producto_raw = productos.find_one({"_id": ObjectId(id)})
    if not producto_raw:
        abort(404, description="Este producto no existe.")
    producto_raw = asegurar_stock_legacy(producto_raw)
    producto = normalizar_producto(producto_raw)

    if producto["drop_pendiente"]:
        flash("Este producto todavía no está disponible.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    if producto["precio"] <= 0:
        flash("Este producto no tiene precio disponible.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    if not producto_comprable(producto_raw):
        if producto["stock"] > 0:
            flash("Sin disponibilidad temporal: otras personas reservaron las unidades disponibles.", "warning")
        else:
            flash("Este producto está agotado.", "warning")
        return redirect(url_for("detalle_producto", id=id))

    talla = request.form.get("talla", "").strip()
    color = request.form.get("color", "").strip()
    variantes = producto.get("variantes", [])
    if variantes and not any(
        v["talla"] == talla and v["color"] == color
        for v in variantes
    ):
        flash("Selecciona una talla y color válidos.", "warning")
        return redirect(url_for("detalle_producto", id=id))

    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (TypeError, ValueError):
        cantidad = 1
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        flash(f"La cantidad debe estar entre 1 y {CARRITO_MAX_CANTIDAD}.", "warning")
        return redirect(url_for("detalle_producto", id=id))

    reserva_id = obtener_reserva_id()
    reservado = reservar_cantidad(id, reserva_id, cantidad, talla, color)
    if reservado:
        flash(
            f"Producto reservado durante {TIEMPO_RESERVA_MINUTOS} minutos.",
            "success",
        )
    else:
        flash(
            "No hay suficientes unidades disponibles para completar esa reserva.",
            "warning",
        )
    return redirect(url_for("ver_carrito"))


@app.route("/carrito/actualizar", methods=["POST"])
def actualizar_carrito():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    producto_id = request.form.get("linea_id", "").strip()
    if not reserva_id or not ObjectId.is_valid(producto_id):
        flash("No se encontró la reserva que quieres actualizar.", "warning")
        return redirect(url_for("ver_carrito"))
    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (TypeError, ValueError):
        cantidad = 1
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        flash(f"La cantidad debe estar entre 1 y {CARRITO_MAX_CANTIDAD}.", "warning")
        return redirect(url_for("ver_carrito"))

    talla = request.form.get("talla", "").strip()
    color = request.form.get("color", "").strip()
    if actualizar_reserva_cantidad(reserva_id, producto_id, cantidad, talla, color):
        flash("Cantidad reservada actualizada correctamente.", "success")
    else:
        flash("No se pudo actualizar la reserva: revisa las existencias disponibles o vuelve a reservar.", "warning")
    return redirect(url_for("ver_carrito"))


@app.route("/carrito/eliminar", methods=["POST"])
def eliminar_carrito():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    producto_id = request.form.get("linea_id", "").strip()
    if not reserva_id or not ObjectId.is_valid(producto_id):
        flash("No se encontró la reserva.", "warning")
        return redirect(url_for("ver_carrito"))

    talla = request.form.get("talla", "").strip()
    color = request.form.get("color", "").strip()
    if eliminar_reserva(reserva_id, producto_id, talla, color):
        flash("Reserva cancelada y producto liberado.", "success")
    else:
        flash("La reserva ya venció o no existe.", "warning")
    return redirect(url_for("ver_carrito"))


# -------------------------------- CUPONES -----------------------------------
@app.route("/checkout/cupon", methods=["POST"])
@requiere_login
def aplicar_cupon():
    liberar_reservas_vencidas()
    _, subtotal = obtener_articulos_carrito()
    codigo = request.form.get("codigo", "").strip().upper()
    cupon = cupones.find_one({"codigo": codigo}) if codigo else None
    valido, mensaje = es_cupon_valido(cupon, subtotal, str(usuario_actual()["_id"]))
    if valido:
        session["cupon_codigo"] = codigo
        flash("Cupón aplicado correctamente.", "success")
    else:
        session.pop("cupon_codigo", None)
        flash(mensaje, "warning")
    return redirect(url_for("checkout"))


@app.route("/checkout/cupon/quitar", methods=["POST"])
def quitar_cupon():
    session.pop("cupon_codigo", None)
    flash("Cupón retirado.", "info")
    return redirect(url_for("checkout"))


# -------------------------------- CHECKOUT ----------------------------------
@app.route("/checkout")
@requiere_login
def checkout():
    liberar_reservas_vencidas()
    articulos, subtotal = obtener_articulos_carrito()
    if not articulos:
        flash("Tu carrito está vacío o sus productos dejaron de estar disponibles.", "info")
        return redirect(url_for("ver_carrito"))
    cupon, descuento = descuento_cupon(subtotal, str(usuario_actual()["_id"]))
    return render_template("checkout.html", articulos=articulos, total=subtotal, subtotal=subtotal, cupon=cupon, descuento=descuento, total_final=round(subtotal-descuento, 2))


@app.route("/checkout/confirmar", methods=["POST"])
@requiere_login
def confirmar_checkout():
    liberar_reservas_vencidas()
    reserva_id = session.get("reserva_id")
    if not reserva_id:
        flash("Tu reserva ya no está activa. Agrega nuevamente los productos.", "warning")
        return redirect(url_for("ver_carrito"))

    usuario = usuario_actual()
    metodo = request.form.get("metodo_pago", "tarjeta_demo")
    if metodo not in METODOS_PAGO:
        flash("Selecciona un método de pago de demostración válido.", "warning")
        return redirect(url_for("checkout"))

    ahora = ahora_utc()
    pedido_id = None
    subtotal = 0.0
    descuento = 0.0
    cupon_usado = None
    try:
        with cliente.start_session() as mongo_session:
            with mongo_session.start_transaction():
                items_reservados = list(reservas.find({
                    "reserva_id": reserva_id,
                    "estado": "temporal",
                    "vence_en": {"$gt": ahora},
                }, session=mongo_session))
                if not items_reservados:
                    raise ValueError("La reserva venció o ya no contiene productos.")

                detalle = []
                for reserva in items_reservados:
                    pid = reserva["producto_id"]
                    cantidad = entero_seguro(reserva.get("cantidad", 0))
                    talla = str(reserva.get("talla", ""))
                    color = str(reserva.get("color", ""))
                    if cantidad < 1:
                        raise ValueError("Una de las reservas no contiene una cantidad válida.")

                    producto = productos.find_one({"_id": pid}, session=mongo_session)
                    if not producto:
                        raise ValueError("Uno de los productos reservados ya no existe.")
                    variantes = producto.get("variantes") or []
                    if variantes:
                        filtro = {
                            "_id": pid,
                            "variantes": {
                                "$elemMatch": {
                                    "talla": talla,
                                    "color": color,
                                    "stock": {"$gte": cantidad},
                                    "reservadas": {"$gte": cantidad},
                                }
                            },
                        }
                        resultado = productos.update_one(
                            filtro,
                            {
                                "$inc": {
                                    "variantes.$[v].stock": -cantidad,
                                    "variantes.$[v].reservadas": -cantidad,
                                }
                            },
                            array_filters=[{
                                "v.talla": talla,
                                "v.color": color,
                                "v.stock": {"$gte": cantidad},
                                "v.reservadas": {"$gte": cantidad},
                            }],
                            session=mongo_session,
                        )
                    else:
                        # Una reserva activa ya inicializó stock y reservadas para este producto.
                        resultado = productos.update_one(
                            {
                                "_id": pid,
                                "stock": {"$gte": cantidad},
                                "reservadas": {"$gte": cantidad},
                            },
                            {
                                "$inc": {
                                    "stock": -cantidad,
                                    "reservadas": -cantidad,
                                }
                            },
                            session=mongo_session,
                        )
                    if resultado.modified_count != 1:
                        raise ValueError("Una reserva ya no coincide con el inventario. No se creó el pedido.")

                    producto_actualizado = productos.find_one({"_id": pid}, session=mongo_session)
                    stock_restante = stock_de_producto(producto_actualizado or {})
                    productos.update_one(
                        {"_id": pid},
                        {"$set": {"estado": "Disponible" if stock_restante > 0 else "Agotado"}},
                        session=mongo_session,
                    )

                    precio = precio_seguro(producto.get("precio", 0))
                    subtotal_item = round(precio * cantidad, 2)
                    subtotal += subtotal_item
                    detalle.append({
                        "producto_id": str(pid),
                        "nombre": producto.get("nombre", "Producto"),
                        "categoria": producto.get("categoria", "Sin categoría"),
                        "cantidad": cantidad,
                        "precio_unitario": precio,
                        "subtotal": subtotal_item,
                        "talla": talla,
                        "color": color,
                    })

                subtotal = round(subtotal, 2)
                codigo_cupon = str(session.get("cupon_codigo", "") or "").strip().upper()
                if codigo_cupon:
                    cupon = cupones.find_one({"codigo": codigo_cupon}, session=mongo_session)
                    valido, _mensaje = es_cupon_valido(
                        cupon, subtotal, str(usuario["_id"])
                    )
                    if valido:
                        valor = precio_seguro(cupon.get("valor", 0))
                        if cupon.get("tipo") == "porcentaje":
                            descuento = subtotal * min(valor, 100) / 100
                        else:
                            descuento = min(valor, subtotal)
                        descuento = round(min(subtotal, max(0, descuento)), 2)
                        cupon_usado = cupon

                pedido = {
                    "cliente": {
                        "nombre": usuario.get("nombre", "Cliente"),
                        "correo": usuario.get("correo", ""),
                    },
                    "usuario_id": str(usuario["_id"]),
                    "items": detalle,
                    "subtotal": subtotal,
                    "descuento": descuento,
                    "cupon_codigo": cupon_usado.get("codigo") if cupon_usado else "",
                    "total": round(max(0, subtotal - descuento), 2),
                    "metodo_pago": METODOS_PAGO[metodo],
                    "estado_pago": "DEMO: no se realizó ningún cobro",
                    "estado_pedido": "Recibido",
                    "creado_en": ahora,
                }
                resultado_pedido = pedidos.insert_one(pedido, session=mongo_session)
                reservas_confirmadas = reservas.update_many(
                    {
                        "reserva_id": reserva_id,
                        "estado": "temporal",
                        "vence_en": {"$gt": ahora},
                    },
                    {"$set": {
                        "estado": "confirmada",
                        "confirmada_en": ahora,
                        "pedido_id": resultado_pedido.inserted_id,
                    }},
                    session=mongo_session,
                )
                if reservas_confirmadas.matched_count != len(items_reservados):
                    raise ValueError("Las reservas cambiaron durante la confirmación. Inténtalo nuevamente.")
                if cupon_usado:
                    cupones.update_one(
                        {"_id": cupon_usado["_id"]},
                        {
                            "$inc": {"usos": 1},
                            "$addToSet": {"usuarios_que_lo_usaron": str(usuario["_id"])},
                        },
                        session=mongo_session,
                    )
                pedido_id = resultado_pedido.inserted_id
    except ValueError as error:
        liberar_reservas_vencidas()
        flash(str(error), "warning")
        return redirect(url_for("ver_carrito"))

    session.pop("reserva_id", None)
    session.pop("carrito", None)
    session.pop("cupon_codigo", None)
    flash("Pedido de demostración creado. No se realizó ningún cobro.", "success")
    return redirect(url_for("confirmacion_compra", id=str(pedido_id)))


@app.route("/compra/confirmacion/<id>")
@requiere_login
def confirmacion_compra(id):
    if not ObjectId.is_valid(id):
        abort(404, description="El número de pedido no es válido.")
    pedido = pedidos.find_one({"_id": ObjectId(id)})
    if not pedido:
        abort(404, description="El pedido no existe.")
    usuario = usuario_actual()
    if not es_administrador(usuario) and pedido.get("usuario_id") != str(usuario["_id"]):
        abort(404, description="El pedido no existe.")
    return render_template("confirmacion.html", pedido=pedido)


# ------------------------------ HISTORIAL -----------------------------------
@app.route("/mis-pedidos")
@requiere_login
def mis_pedidos():
    usuario = usuario_actual()
    lista = list(pedidos.find({"usuario_id": str(usuario["_id"])}).sort("creado_en", -1).limit(100))
    return render_template("mis_pedidos.html", pedidos=lista)


@app.route("/mis-pedidos/<id>")
@requiere_login
def detalle_pedido_cliente(id):
    if not ObjectId.is_valid(id):
        abort(404, description="El pedido no existe.")
    pedido = pedidos.find_one({"_id": ObjectId(id)})
    usuario = usuario_actual()
    if not pedido or (not es_administrador(usuario) and pedido.get("usuario_id") != str(usuario["_id"])):
        abort(404, description="El pedido no existe.")
    return render_template("pedido_detalle.html", pedido=pedido, modo_admin=False)


# ------------------------------ RESEÑAS -------------------------------------
@app.route("/producto/<id>/resenas", methods=["POST"])
@requiere_login
def guardar_resena(id):
    producto = buscar_producto(id)
    usuario = usuario_actual()
    compra_verificada = pedidos.find_one({"usuario_id": str(usuario["_id"]), "items.producto_id": str(producto["_id"])})
    if not compra_verificada:
        flash("Para dejar una reseña primero debes completar un pedido de este producto.", "warning")
        return redirect(url_for("detalle_producto", id=id) + "#resenas")
    try:
        calificacion = int(request.form.get("calificacion", "5"))
    except ValueError:
        calificacion = 0
    comentario = request.form.get("comentario", "").strip()
    if calificacion < 1 or calificacion > 5:
        flash("La calificación debe ser de 1 a 5 estrellas.", "warning")
    elif not comentario or len(comentario) > 800:
        flash("Escribe un comentario de hasta 800 caracteres.", "warning")
    else:
        resenas.update_one(
            {"producto_id": str(producto["_id"]), "usuario_id": str(usuario["_id"])},
            {"$set": {"producto_id": str(producto["_id"]), "producto_nombre": producto["nombre"], "usuario_id": str(usuario["_id"]), "usuario_nombre": usuario.get("nombre", "Cliente"), "calificacion": calificacion, "comentario": comentario, "visible": True, "actualizado_en": ahora_utc()}, "$setOnInsert": {"creado_en": ahora_utc()}},
            upsert=True,
        )
        flash("Gracias por dejar tu reseña.", "success")
    return redirect(url_for("detalle_producto", id=id) + "#resenas")


# ------------------------------ ADMIN ---------------------------------------
def preservar_reservas_en_edicion(datos, producto_actual):
    """No permite que el formulario administrativo borre unidades ya apartadas."""
    anteriores = producto_actual.get("variantes") or []
    nuevas = datos.get("variantes") or []
    anteriores_por_clave = {
        (str(v.get("talla", "")), str(v.get("color", ""))): v
        for v in anteriores
        if isinstance(v, dict)
    }

    if anteriores and nuevas:
        nuevas_procesadas = []
        claves_nuevas = set()
        for variante in nuevas:
            clave = (str(variante.get("talla", "")), str(variante.get("color", "")))
            anterior = anteriores_por_clave.get(clave, {})
            reservadas = entero_seguro(anterior.get("reservadas", 0))
            if entero_seguro(variante.get("stock", 0)) < reservadas:
                abort(400, description=f"No puedes bajar el stock de {clave[0]} / {clave[1]} por debajo de sus {reservadas} unidad(es) reservadas.")
            nuevas_procesadas.append({**variante, "reservadas": reservadas})
            claves_nuevas.add(clave)
        for clave, anterior in anteriores_por_clave.items():
            reservadas = entero_seguro(anterior.get("reservadas", 0))
            if reservadas and clave not in claves_nuevas:
                abort(400, description=f"No puedes eliminar la variante {clave[0]} / {clave[1]} porque tiene {reservadas} unidad(es) reservadas.")
        datos["variantes"] = nuevas_procesadas
        datos["stock"] = sum(v["stock"] for v in nuevas_procesadas)
        datos["reservadas"] = sum(v["reservadas"] for v in nuevas_procesadas)

    elif anteriores and not nuevas:
        reservadas_anteriores = sum(
            entero_seguro(v.get("reservadas", 0)) for v in anteriores
            if isinstance(v, dict)
        )
        if reservadas_anteriores:
            abort(400, description="No puedes cambiar a inventario simple mientras existan variantes reservadas.")
        datos["reservadas"] = 0

    elif not anteriores and nuevas:
        reservadas_anteriores = entero_seguro(producto_actual.get("reservadas", 0))
        if reservadas_anteriores:
            abort(400, description="No puedes convertir a variantes mientras haya unidades simples reservadas.")
        datos["variantes"] = [{**v, "reservadas": 0} for v in nuevas]
        datos["stock"] = sum(v["stock"] for v in datos["variantes"])
        datos["reservadas"] = 0

    else:
        reservadas = entero_seguro(producto_actual.get("reservadas", 0))
        if entero_seguro(datos.get("stock", 0)) < reservadas:
            abort(400, description=f"No puedes reducir las existencias por debajo de las {reservadas} unidad(es) reservadas.")
        datos["reservadas"] = reservadas

    datos["estado"] = "Disponible" if entero_seguro(datos.get("stock", 0)) > 0 else "Agotado"
    return datos


def actualizar_producto_preservando_reservas(producto_id, datos):
    """Actualiza inventario en transacción para no pisar reservas simultáneas."""
    with cliente.start_session() as mongo_session:
        with mongo_session.start_transaction():
            actual = productos.find_one({"_id": producto_id}, session=mongo_session)
            if not actual:
                return False
            datos_seguros = preservar_reservas_en_edicion(dict(datos), actual)
            datos_seguros["creado_en"] = actual.get("creado_en", ahora_utc())
            resultado = productos.update_one(
                {"_id": producto_id},
                {"$set": datos_seguros},
                session=mongo_session,
            )
            return resultado.matched_count == 1


@app.route("/admin", endpoint="inicio")
@requiere_admin
def panel_admin():
    liberar_reservas_vencidas()
    lista_raw = list(productos.find().sort("nombre", 1))
    lista = [normalizar_producto(p) for p in lista_raw]
    total_ventas = sum(precio_seguro(p.get("total", 0)) for p in pedidos.find({}, {"total": 1}))
    total_pedidos = pedidos.count_documents({})
    pendientes = pedidos.count_documents({"estado_pedido": {"$in": ["Recibido", "Preparando"]}})
    poco_stock = sum(1 for p in lista if p["stock"] <= 3)
    top_productos = {}
    for pedido in pedidos.find({}, {"items": 1}):
        for item in pedido.get("items", []):
            nombre = item.get("nombre", "Producto")
            top_productos[nombre] = top_productos.get(nombre, 0) + entero_seguro(item.get("cantidad", 0))
    top = sorted(top_productos.items(), key=lambda x: x[1], reverse=True)[:5]
    stats = {"productos": len(lista), "usuarios": usuarios.count_documents({}), "pedidos": total_pedidos, "pendientes": pendientes, "poco_stock": poco_stock, "ventas": round(total_ventas, 2), "top": top}
    return render_template("index.html", alumnos=lista, stats=stats)


@app.route("/agregar", methods=["POST"])
@requiere_admin
def agregar():
    datos = leer_formulario()
    datos["creado_en"] = ahora_utc()
    productos.insert_one(datos)
    flash("Producto guardado en el inventario.", "success")
    return redirect(url_for("inicio") + "#lista")


@app.route("/editar/<id>", methods=["GET", "POST"])
@requiere_admin
def editar(id):
    liberar_reservas_vencidas()
    producto = buscar_producto(id)
    if request.method == "POST":
        datos = leer_formulario()
        if not actualizar_producto_preservando_reservas(producto["_id"], datos):
            flash("El producto ya no existe o no se pudo actualizar.", "warning")
            return redirect(url_for("inicio") + "#lista")
        flash("Producto actualizado.", "success")
        return redirect(url_for("inicio") + "#lista")
    variantes_texto = "\n".join(f"{v['talla']} | {v['color']} | {v['stock']}" for v in producto.get("variantes", []))
    return render_template("editar.html", alumno=producto, variantes_texto=variantes_texto)


@app.route("/eliminar/<id>", methods=["POST"])
@requiere_admin
def eliminar(id):
    liberar_reservas_vencidas()
    producto = buscar_producto(id)
    if reservas.count_documents({"producto_id": producto["_id"], "estado": "temporal"}):
        flash("No puedes eliminar este producto mientras tenga reservas activas.", "warning")
        return redirect(url_for("inicio") + "#lista")
    productos.delete_one({"_id": producto["_id"]})
    resenas.delete_many({"producto_id": str(producto["_id"])})
    flash("Producto eliminado del inventario.", "info")
    return redirect(url_for("inicio") + "#lista")


@app.route("/admin/pedidos")
@requiere_admin
def admin_pedidos():
    lista = list(pedidos.find().sort("creado_en", -1).limit(200))
    return render_template("admin_pedidos.html", pedidos=lista)


@app.route("/admin/pedidos/<id>/estado", methods=["POST"])
@requiere_admin
def admin_actualizar_pedido(id):
    estado = request.form.get("estado_pedido", "")
    if not ObjectId.is_valid(id) or estado not in ESTADOS_PEDIDO:
        abort(400, description="El pedido o su estado no son válidos.")
    pedidos.update_one({"_id": ObjectId(id)}, {"$set": {"estado_pedido": estado, "actualizado_en": ahora_utc()}})
    flash("Estado del pedido actualizado.", "success")
    return redirect(url_for("admin_pedidos"))


@app.route("/admin/cupones", methods=["GET", "POST"])
@requiere_admin
def admin_cupones():
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        tipo = request.form.get("tipo", "porcentaje")
        try:
            valor = float(request.form.get("valor", "0"))
            minimo = float(request.form.get("compra_minima", "0") or 0)
            max_usos = int(request.form.get("uso_maximo", "0") or 0)
        except ValueError:
            flash("Valor, compra mínima y límite de usos deben ser números.", "warning")
            return redirect(url_for("admin_cupones"))
        if not re.fullmatch(r"[A-Z0-9_-]{3,30}", codigo):
            flash("El código debe tener entre 3 y 30 caracteres: letras, números, guion o guion bajo.", "warning")
            return redirect(url_for("admin_cupones"))
        if tipo not in ("porcentaje", "fijo") or valor <= 0 or (tipo == "porcentaje" and valor > 100) or minimo < 0 or max_usos < 0:
            flash("Revisa el tipo, valor, compra mínima y límite de usos.", "warning")
            return redirect(url_for("admin_cupones"))
        fecha_inicio = fecha_utc(request.form.get("fecha_inicio", ""))
        fecha_fin = fecha_utc(request.form.get("fecha_fin", ""))
        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            flash("La fecha final debe ser posterior a la fecha inicial.", "warning")
            return redirect(url_for("admin_cupones"))
        try:
            cupones.insert_one({"codigo": codigo, "tipo": tipo, "valor": valor, "compra_minima": minimo, "uso_maximo": max_usos, "usos": 0, "activo": True, "fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin, "usuarios_que_lo_usaron": [], "creado_en": ahora_utc()})
            flash("Cupón creado.", "success")
        except DuplicateKeyError:
            flash("Ya existe un cupón con ese código.", "warning")
        return redirect(url_for("admin_cupones"))
    cupones.create_index("codigo", unique=True)
    lista = list(cupones.find().sort("creado_en", -1))
    return render_template("admin_cupones.html", cupones=lista)


@app.route("/admin/cupones/<id>/alternar", methods=["POST"])
@requiere_admin
def admin_alternar_cupon(id):
    if not ObjectId.is_valid(id):
        abort(404)
    cupon = cupones.find_one({"_id": ObjectId(id)})
    if not cupon:
        abort(404)
    cupones.update_one({"_id": cupon["_id"]}, {"$set": {"activo": not cupon.get("activo", True)}})
    flash("Estado del cupón actualizado.", "success")
    return redirect(url_for("admin_cupones"))


@app.route("/admin/resenas")
@requiere_admin
def admin_resenas():
    lista = list(resenas.find().sort("creado_en", -1).limit(300))
    return render_template("admin_resenas.html", resenas=lista)


@app.route("/admin/resenas/<id>/visibilidad", methods=["POST"])
@requiere_admin
def admin_resena_visibilidad(id):
    if not ObjectId.is_valid(id):
        abort(404)
    resena = resenas.find_one({"_id": ObjectId(id)})
    if not resena:
        abort(404)
    resenas.update_one({"_id": resena["_id"]}, {"$set": {"visible": not resena.get("visible", True)}})
    flash("Visibilidad de la reseña actualizada.", "success")
    return redirect(url_for("admin_resenas"))


@app.route("/ayuda")
def ayuda():
    return render_template("ayuda.html", fecha=ahora_utc().strftime("%d/%m/%Y"))


# -------------------------------- API JSON ----------------------------------
@app.route("/api/catalogo", methods=["GET"])
def api_catalogo():
    liberar_reservas_vencidas()
    lista, *_ = obtener_catalogo_filtrado(request.args)
    return jsonify([producto_json(p) for p in lista[:100]])


@app.route("/api/productos", methods=["GET", "POST"])
def api_productos():
    if request.method == "POST":
        liberar_reservas_vencidas()
        if not usuario_actual():
            return jsonify({"error": "Inicia sesión como administrador para crear productos."}), 401
        if not es_administrador():
            return jsonify({"error": "No tienes permisos para modificar el inventario."}), 403
        datos = leer_json()
        datos["creado_en"] = ahora_utc()
        resultado = productos.insert_one(datos)
        return jsonify(producto_json(buscar_producto(str(resultado.inserted_id)))), 201
    lista = productos.find().sort("nombre", 1).limit(100)
    return jsonify([producto_json(p) for p in lista])


@app.route("/api/productos/<id>", methods=["GET", "PUT", "DELETE"])
def api_producto(id):
    liberar_reservas_vencidas()
    producto = buscar_producto(id)
    if request.method == "PUT":
        if not usuario_actual():
            return jsonify({"error": "Inicia sesión como administrador para editar productos."}), 401
        if not es_administrador():
            return jsonify({"error": "No tienes permisos para modificar el inventario."}), 403
        datos = leer_json()
        if not actualizar_producto_preservando_reservas(producto["_id"], datos):
            return jsonify({"error": "El producto ya no existe o no se pudo actualizar."}), 409
        return jsonify(producto_json(buscar_producto(id)))
    if request.method == "DELETE":
        if not usuario_actual():
            return jsonify({"error": "Inicia sesión como administrador para eliminar productos."}), 401
        if not es_administrador():
            return jsonify({"error": "No tienes permisos para modificar el inventario."}), 403
        if reservas.count_documents({"producto_id": producto["_id"], "estado": "temporal"}):
            return jsonify({"error": "No se puede eliminar un producto con reservas activas."}), 409
        productos.delete_one({"_id": producto["_id"]})
        return "", 204
    return jsonify(producto_json(producto))


@app.errorhandler(PyMongoError)
def error_mongo(error):
    mensaje = "No fue posible conectar con la base de datos. Revisa la configuración y vuelve a intentar."
    if request.path.startswith("/api/"):
        return jsonify({"error": mensaje}), 503
    return render_template("error.html", mensaje=mensaje), 503


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
def error_peticion(error):
    mensaje = getattr(error, "description", "La solicitud no se pudo completar.")
    if request.path.startswith("/api/"):
        return jsonify({"error": mensaje}), error.code
    return render_template("error.html", mensaje=mensaje), error.code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
