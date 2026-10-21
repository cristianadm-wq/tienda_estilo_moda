import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from hmac import compare_digest
from urllib.parse import quote, unquote, urlparse

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash

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

cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
base_datos = cliente["tienda_estilo_moda"]
productos = base_datos["productos"]
pedidos = base_datos["pedidos"]
usuarios = base_datos["usuarios"]
cupones = base_datos["cupones"]
resenas = base_datos["resenas"]
alumnos = productos  # Alias para conservar compatibilidad con la práctica CRUD.

ESTADOS_VALIDOS = ("Disponible", "Agotado")
ESTADOS_PEDIDO = ("Recibido", "Preparando", "Enviado", "Entregado", "Cancelado")
METODOS_PAGO = {
    "tarjeta_demo": "Tarjeta de demostración (sin introducir datos)",
    "transferencia_demo": "Transferencia simulada",
    "contra_entrega_demo": "Pago contra entrega (demostración)",
}
CARRITO_MAX_CANTIDAD = 10


def ahora_utc():
    return datetime.now(timezone.utc)


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
    variantes = producto.get("variantes") or []
    if isinstance(variantes, list) and variantes:
        return sum(entero_seguro(v.get("stock", 0)) for v in variantes if isinstance(v, dict))
    if "stock" in producto:
        return entero_seguro(producto.get("stock", 0))
    # Compatibilidad para los productos anteriores, que no tenían inventario numérico.
    return 10 if producto.get("estado") == "Disponible" else 0


def normalizar_producto(producto):
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
        if isinstance(variante, dict):
            variantes_limpias.append({
                "talla": str(variante.get("talla", "Única")),
                "color": str(variante.get("color", "Único")),
                "stock": entero_seguro(variante.get("stock", 0)),
            })
    resultado["variantes"] = variantes_limpias
    resultado["stock"] = stock_de_producto(resultado)
    resultado["estado"] = "Disponible" if resultado["stock"] > 0 else "Agotado"
    creado = fecha_utc(resultado.get("creado_en")) or datetime(2000, 1, 1, tzinfo=timezone.utc)
    drop_at = fecha_utc(resultado.get("drop_at"))
    resultado["creado_en"] = creado
    resultado["drop_at_dt"] = drop_at
    resultado["drop_at_iso"] = drop_at.isoformat() if drop_at else ""
    resultado["drop_pendiente"] = bool(drop_at and drop_at > ahora_utc())
    resultado["es_nuevo"] = creado >= ahora_utc() - timedelta(days=14)
    resultado["en_oferta"] = bool(resultado["precio_original"] > resultado["precio"] > 0)
    resultado["porcentaje_descuento"] = int(round((1 - resultado["precio"] / resultado["precio_original"]) * 100)) if resultado["en_oferta"] else 0
    return resultado


def inventario_variante(producto, talla="", color=""):
    variantes = producto.get("variantes") or []
    if variantes:
        for variante in variantes:
            if str(variante.get("talla", "")) == str(talla) and str(variante.get("color", "")) == str(color):
                return entero_seguro(variante.get("stock", 0))
        return 0
    if producto.get("estado") != "Disponible":
        return 0
    if "stock" in producto:
        return entero_seguro(producto.get("stock", 0))
    return 10


def asegurar_stock_legacy(producto):
    if not producto.get("variantes") and "stock" not in producto:
        inicial = 10 if producto.get("estado") == "Disponible" else 0
        productos.update_one({"_id": producto["_id"], "stock": {"$exists": False}}, {"$set": {"stock": inicial}})
        producto["stock"] = inicial
    return producto


def producto_comprable(producto):
    producto = normalizar_producto(producto)
    return producto["estado"] == "Disponible" and not producto["drop_pendiente"] and producto["precio"] > 0


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
        variantes.append({"talla": talla, "color": color, "stock": stock})
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
        "descripcion": p["descripcion"], "imagen": p["imagen"], "stock": p["stock"],
        "variantes": p["variantes"], "creado_en": p["creado_en"].isoformat(),
        "drop_at": p["drop_at_iso"], "drop_pendiente": p["drop_pendiente"],
    }


def leer_json():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        abort(400, description="Envía un objeto JSON válido.")
    return leer_formulario(datos)


def parsear_clave_carrito(clave, valor):
    # Compatibilidad con carritos antiguos que guardaban solamente {id: cantidad}.
    if isinstance(clave, str) and "|" in clave:
        partes = clave.split("|", 2)
        if len(partes) == 3:
            return partes[0], unquote(partes[1]), unquote(partes[2])
    if isinstance(valor, dict):
        return str(clave), str(valor.get("talla", "")), str(valor.get("color", ""))
    return str(clave), "", ""


def clave_carrito(id_producto, talla="", color=""):
    return f"{id_producto}|{quote(str(talla), safe='')}|{quote(str(color), safe='')}"


def carrito_sesion():
    carrito = session.get("carrito", {})
    return carrito if isinstance(carrito, dict) else {}


def cantidad_carrito():
    total = 0
    for valor in carrito_sesion().values():
        cantidad = valor.get("cantidad", 1) if isinstance(valor, dict) else valor
        total += min(CARRITO_MAX_CANTIDAD, entero_seguro(cantidad, 1))
    return total


def obtener_articulos_carrito():
    carrito = carrito_sesion()
    articulos = []
    nuevo = {}
    total = 0.0
    for clave, valor in carrito.items():
        id_producto, talla, color = parsear_clave_carrito(clave, valor)
        if not ObjectId.is_valid(id_producto):
            continue
        producto_raw = productos.find_one({"_id": ObjectId(id_producto)})
        if not producto_raw:
            continue
        producto = normalizar_producto(producto_raw)
        try:
            cantidad = int(valor.get("cantidad", 1) if isinstance(valor, dict) else valor)
        except (TypeError, ValueError):
            cantidad = 1
        cantidad = max(1, min(cantidad, CARRITO_MAX_CANTIDAD))
        stock = inventario_variante(producto_raw, talla, color)
        if not producto_comprable(producto_raw) or stock <= 0:
            continue
        cantidad = min(cantidad, stock)
        if cantidad < 1:
            continue
        nueva_clave = clave_carrito(id_producto, talla, color)
        nuevo[nueva_clave] = {"cantidad": cantidad, "talla": talla, "color": color}
        precio = producto["precio"]
        subtotal = round(precio * cantidad, 2)
        articulos.append({**producto, "cantidad": cantidad, "talla": talla, "color": color, "linea_id": nueva_clave, "subtotal": subtotal})
        total += subtotal
    if nuevo != carrito:
        session["carrito"] = nuevo
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
        carrito_guardado = carrito_sesion()
        session.clear()
        session["carrito"] = carrito_guardado
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
        carrito_guardado = carrito_sesion()
        session.clear()
        session["carrito"] = carrito_guardado
        session["usuario_id"] = str(usuario["_id"])
        session.permanent = True
        g.usuario_actual = usuario
        flash(f"Bienvenido/a, {usuario.get('nombre', 'usuario')}.", "success")
        return redireccion_interna_o_tienda(request.form.get("next", ""))
    return render_template("login.html", next_url=destino)


@app.route("/salir", methods=["POST"])
def salir():
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
    lista, q, categoria, estado, minimo, maximo, orden = obtener_catalogo_filtrado(request.args)
    categorias = sorted([c for c in productos.distinct("categoria") if isinstance(c, str) and c.strip()])
    filtros = {"q": q, "categoria": categoria, "estado": estado, "min_precio": minimo, "max_precio": maximo, "orden": orden}
    return render_template("tienda.html", productos=lista, busqueda=q, categorias=categorias, filtros=filtros, next_url=(request.full_path or "/") + "#catalogo")


@app.route("/catalogo/resultados")
def resultados_catalogo():
    lista, q, categoria, estado, minimo, maximo, orden = obtener_catalogo_filtrado(request.args)
    return render_template("_product_grid.html", productos=lista, busqueda=q, next_url=url_for("tienda") + (("?" + request.query_string.decode("utf-8")) if request.query_string else "") + "#catalogo")


@app.route("/producto/<id>")
def detalle_producto(id):
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
    articulos, subtotal = obtener_articulos_carrito()
    cupon, descuento = descuento_cupon(subtotal, str(usuario_actual()["_id"]) if usuario_actual() else None)
    return render_template("carrito.html", articulos=articulos, total=subtotal, subtotal=subtotal, cupon=cupon, descuento=descuento, total_final=round(subtotal-descuento, 2))


@app.route("/carrito/agregar/<id>", methods=["POST"])
def agregar_carrito(id):
    producto_raw = productos.find_one({"_id": ObjectId(id)}) if ObjectId.is_valid(id) else None
    if not producto_raw:
        abort(404, description="Este producto no existe.")
    producto = normalizar_producto(producto_raw)
    if producto["drop_pendiente"]:
        flash("Este drop todavía no está disponible.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    if not producto_comprable(producto_raw):
        flash("Este producto no está disponible para comprar.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    if producto["precio"] <= 0:
        flash("El administrador debe asignar un precio antes de vender este producto.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    talla = request.form.get("talla", "").strip()
    color = request.form.get("color", "").strip()
    variantes = producto.get("variantes", [])
    if variantes and not any(v["talla"] == talla and v["color"] == color for v in variantes):
        flash("Selecciona una combinación válida de talla y color.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (TypeError, ValueError):
        cantidad = 1
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        flash(f"La cantidad debe estar entre 1 y {CARRITO_MAX_CANTIDAD}.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    producto_raw = asegurar_stock_legacy(producto_raw)
    stock = inventario_variante(producto_raw, talla, color)
    clave = clave_carrito(id, talla, color)
    carrito = carrito_sesion()
    valor_anterior = carrito.get(clave, {})
    cantidad_anterior = entero_seguro(valor_anterior.get("cantidad", 0) if isinstance(valor_anterior, dict) else valor_anterior)
    nueva = cantidad_anterior + cantidad
    if nueva > stock:
        flash(f"Solo hay {stock} unidad(es) disponibles de esta combinación.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    if nueva > CARRITO_MAX_CANTIDAD:
        flash(f"Puedes agregar máximo {CARRITO_MAX_CANTIDAD} unidades por combinación.", "warning")
        return redirect(url_for("ver_carrito"))
    carrito[clave] = {"cantidad": nueva, "talla": talla, "color": color}
    session["carrito"] = carrito
    flash(f"{producto['nombre']} se agregó al carrito.", "success")
    return redirect(url_for("ver_carrito"))


@app.route("/carrito/actualizar", methods=["POST"])
def actualizar_carrito():
    clave = request.form.get("linea_id", "")
    carrito = carrito_sesion()
    if clave not in carrito:
        return redirect(url_for("ver_carrito"))
    pid, talla, color = parsear_clave_carrito(clave, carrito[clave])
    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (TypeError, ValueError):
        cantidad = 1
    producto = productos.find_one({"_id": ObjectId(pid)}) if ObjectId.is_valid(pid) else None
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        flash(f"La cantidad debe estar entre 1 y {CARRITO_MAX_CANTIDAD}.", "warning")
    elif not producto or cantidad > inventario_variante(producto, talla, color):
        flash("La cantidad supera las existencias disponibles.", "warning")
    else:
        carrito[clave] = {"cantidad": cantidad, "talla": talla, "color": color}
        session["carrito"] = carrito
        flash("Cantidad actualizada.", "success")
    return redirect(url_for("ver_carrito"))


@app.route("/carrito/eliminar", methods=["POST"])
def eliminar_carrito():
    carrito = carrito_sesion()
    carrito.pop(request.form.get("linea_id", ""), None)
    session["carrito"] = carrito
    flash("Producto eliminado del carrito.", "info")
    return redirect(url_for("ver_carrito"))


# -------------------------------- CUPONES -----------------------------------
@app.route("/checkout/cupon", methods=["POST"])
@requiere_login
def aplicar_cupon():
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
    articulos, subtotal = obtener_articulos_carrito()
    if not articulos:
        flash("Tu carrito está vacío o sus productos dejaron de estar disponibles.", "info")
        return redirect(url_for("ver_carrito"))
    cupon, descuento = descuento_cupon(subtotal, str(usuario_actual()["_id"]))
    return render_template("checkout.html", articulos=articulos, total=subtotal, subtotal=subtotal, cupon=cupon, descuento=descuento, total_final=round(subtotal-descuento, 2))


@app.route("/checkout/confirmar", methods=["POST"])
@requiere_login
def confirmar_checkout():
    articulos, subtotal = obtener_articulos_carrito()
    if not articulos:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for("ver_carrito"))
    usuario = usuario_actual()
    metodo = request.form.get("metodo_pago", "tarjeta_demo")
    if metodo not in METODOS_PAGO:
        flash("Selecciona un método de pago de demostración válido.", "warning")
        return redirect(url_for("checkout"))
    cupon, descuento = descuento_cupon(subtotal, str(usuario["_id"]))
    ahora = ahora_utc()
    descontados = []
    detalle = []
    # Volver a comprobar y reservar existencias justo al confirmar el pedido.
    for item in articulos:
        pid = item["_id"]
        cantidad = item["cantidad"]
        raw = productos.find_one({"_id": pid})
        if not raw or not producto_comprable(raw):
            for revertir in reversed(descontados):
                if revertir["variantes"]:
                    productos.update_one({"_id": revertir["id"]}, {"$inc": {"variantes.$[v].stock": revertir["cantidad"]}}, array_filters=[{"v.talla": revertir["talla"], "v.color": revertir["color"]}])
                else:
                    productos.update_one({"_id": revertir["id"]}, {"$inc": {"stock": revertir["cantidad"]}, "$set": {"estado": "Disponible"}})
            flash("Un producto dejó de estar disponible. Revisa el carrito.", "warning")
            return redirect(url_for("ver_carrito"))
        raw = asegurar_stock_legacy(raw)
        variantes = raw.get("variantes") or []
        talla, color = item.get("talla", ""), item.get("color", "")
        if variantes:
            resultado = productos.update_one(
                {"_id": pid, "variantes": {"$elemMatch": {"talla": talla, "color": color, "stock": {"$gte": cantidad}}}},
                {"$inc": {"variantes.$.stock": -cantidad}},
            )
            usado_variante = True
        else:
            resultado = productos.update_one({"_id": pid, "stock": {"$gte": cantidad}, "estado": "Disponible"}, {"$inc": {"stock": -cantidad}})
            usado_variante = False
        if resultado.modified_count != 1:
            for revertir in reversed(descontados):
                if revertir["variantes"]:
                    productos.update_one({"_id": revertir["id"]}, {"$inc": {"variantes.$[v].stock": revertir["cantidad"]}}, array_filters=[{"v.talla": revertir["talla"], "v.color": revertir["color"]}])
                else:
                    productos.update_one({"_id": revertir["id"]}, {"$inc": {"stock": revertir["cantidad"]}, "$set": {"estado": "Disponible"}})
            flash("No quedan suficientes existencias para una de tus selecciones. Revisa el carrito.", "warning")
            return redirect(url_for("ver_carrito"))
        descontados.append({"id": pid, "cantidad": cantidad, "variantes": usado_variante, "talla": talla, "color": color})
        recargado = productos.find_one({"_id": pid})
        nuevo_total_stock = stock_de_producto(recargado or {})
        productos.update_one({"_id": pid}, {"$set": {"estado": "Disponible" if nuevo_total_stock > 0 else "Agotado"}})
        detalle.append({"producto_id": str(pid), "nombre": item["nombre"], "categoria": item["categoria"], "cantidad": cantidad, "precio_unitario": item["precio"], "subtotal": item["subtotal"], "talla": talla, "color": color})

    total_final = round(max(0, subtotal - descuento), 2)
    pedido = {
        "cliente": {"nombre": usuario.get("nombre", "Cliente"), "correo": usuario.get("correo", "")},
        "usuario_id": str(usuario["_id"]), "items": detalle,
        "subtotal": subtotal, "descuento": descuento, "cupon_codigo": cupon.get("codigo") if cupon else "",
        "total": total_final, "metodo_pago": METODOS_PAGO[metodo],
        "estado_pago": "DEMO: no se realizó ningún cobro", "estado_pedido": "Recibido", "creado_en": ahora,
    }
    try:
        resultado_pedido = pedidos.insert_one(pedido)
    except PyMongoError:
        # Si no se pudo guardar el pedido, devuelve el inventario reservado.
        for revertir in reversed(descontados):
            if revertir["variantes"]:
                productos.update_one({"_id": revertir["id"]}, {"$inc": {"variantes.$[v].stock": revertir["cantidad"]}}, array_filters=[{"v.talla": revertir["talla"], "v.color": revertir["color"]}])
            else:
                productos.update_one({"_id": revertir["id"]}, {"$inc": {"stock": revertir["cantidad"]}, "$set": {"estado": "Disponible"}})
        raise
    if cupon:
        cupones.update_one({"_id": cupon["_id"]}, {"$inc": {"usos": 1}, "$addToSet": {"usuarios_que_lo_usaron": str(usuario["_id"])}})
    session["carrito"] = {}
    session.pop("cupon_codigo", None)
    flash("Pedido de demostración creado. No se realizó ningún cobro.", "success")
    return redirect(url_for("confirmacion_compra", id=str(resultado_pedido.inserted_id)))


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
@app.route("/admin", endpoint="inicio")
@requiere_admin
def panel_admin():
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
    producto = buscar_producto(id)
    if request.method == "POST":
        datos = leer_formulario()
        datos["creado_en"] = productos.find_one({"_id": producto["_id"]}).get("creado_en", ahora_utc())
        productos.update_one({"_id": producto["_id"]}, {"$set": datos})
        flash("Producto actualizado.", "success")
        return redirect(url_for("inicio") + "#lista")
    variantes_texto = "\n".join(f"{v['talla']} | {v['color']} | {v['stock']}" for v in producto.get("variantes", []))
    return render_template("editar.html", alumno=producto, variantes_texto=variantes_texto)


@app.route("/eliminar/<id>", methods=["POST"])
@requiere_admin
def eliminar(id):
    producto = buscar_producto(id)
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
    lista, *_ = obtener_catalogo_filtrado(request.args)
    return jsonify([producto_json(p) for p in lista[:100]])


@app.route("/api/productos", methods=["GET", "POST"])
def api_productos():
    if request.method == "POST":
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
    producto = buscar_producto(id)
    if request.method == "PUT":
        if not usuario_actual():
            return jsonify({"error": "Inicia sesión como administrador para editar productos."}), 401
        if not es_administrador():
            return jsonify({"error": "No tienes permisos para modificar el inventario."}), 403
        datos = leer_json()
        productos.update_one({"_id": producto["_id"]}, {"$set": datos})
        return jsonify(producto_json(buscar_producto(id)))
    if request.method == "DELETE":
        if not usuario_actual():
            return jsonify({"error": "Inicia sesión como administrador para eliminar productos."}), 401
        if not es_administrador():
            return jsonify({"error": "No tienes permisos para modificar el inventario."}), 403
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
