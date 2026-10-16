import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse

from bson import ObjectId
from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request,
    session, url_for,
)
from pymongo import MongoClient
from pymongo.errors import PyMongoError

app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura SECRET_KEY en Render para proteger las sesiones del carrito.")
    secret_key = secrets.token_hex(32)
app.config["SECRET_KEY"] = secret_key

mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb://127.0.0.1:27017/"

cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
base_datos = cliente["tienda_estilo_moda"]
productos = base_datos["productos"]
pedidos = base_datos["pedidos"]

# Alias conservado para que las plantillas y la práctica CRUD existente sigan funcionando.
alumnos = productos

ESTADOS_VALIDOS = ("Disponible", "Agotado")
CARRITO_MAX_CANTIDAD = 10


def precio_seguro(valor, predeterminado=0.0):
    """Convierte un precio en un número válido, redondeado a dos decimales."""
    try:
        cantidad = Decimal(str(valor))
        if not cantidad.is_finite() or cantidad < 0:
            return predeterminado
        return float(cantidad.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return predeterminado


def normalizar_producto(producto):
    """Añade valores por defecto para documentos creados con la versión CRUD anterior."""
    resultado = dict(producto)
    resultado.setdefault("categoria", "Sin categoría")
    resultado.setdefault("estado", "Agotado")
    resultado.setdefault("precio", 0.0)
    resultado["precio"] = precio_seguro(resultado.get("precio", 0))
    resultado.setdefault("descripcion", "Prenda o accesorio streetwear.")
    resultado.setdefault("imagen", "")
    return resultado


def leer_formulario(datos=None):
    """Valida formularios HTML o datos JSON con el mismo esquema."""
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
    if len(nombre) > 80 or len(categoria) > 20:
        abort(400, description="Máximo: nombre 80 y categoría 20.")
    if estado not in ESTADOS_VALIDOS:
        abort(400, description="Selecciona un estado válido.")

    precio_raw = datos.get("precio", "")
    if es_formulario_html and not str(precio_raw).strip():
        abort(400, description="Indica el precio del producto.")
    if precio_raw is None or str(precio_raw).strip() == "":
        precio = 0.0  # Permite las pruebas API de la guía, que envían los tres campos básicos.
    else:
        try:
            precio_decimal = Decimal(str(precio_raw).strip())
            if not precio_decimal.is_finite() or precio_decimal <= 0:
                abort(400, description="El precio debe ser mayor que cero.")
            precio = float(precio_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError):
            abort(400, description="Escribe un precio válido, por ejemplo 1299.00.")

    descripcion = datos.get("descripcion", "")
    imagen = datos.get("imagen", "")
    if not isinstance(descripcion, str) or not isinstance(imagen, str):
        abort(400, description="La descripción y la imagen deben ser texto.")
    descripcion = descripcion.strip()
    imagen = imagen.strip()
    if len(descripcion) > 1200:
        abort(400, description="La descripción admite hasta 1200 caracteres.")
    if imagen:
        partes = urlparse(imagen)
        if partes.scheme not in ("https", "http") or not partes.netloc:
            abort(400, description="La imagen debe usar una dirección HTTP o HTTPS completa.")

    return {
        "nombre": nombre,
        "categoria": categoria,
        "estado": estado,
        "precio": precio,
        "descripcion": descripcion or "Prenda o accesorio streetwear.",
        "imagen": imagen,
    }


def buscar_alumno(id):
    """Nombre mantenido por compatibilidad con las rutas CRUD de la guía."""
    if not ObjectId.is_valid(id):
        abort(404, description="La clave del producto no es válida.")
    producto = productos.find_one({"_id": ObjectId(id)})
    if producto is None:
        abort(404, description="Este producto ya no existe.")
    return normalizar_producto(producto)


def producto_json(producto):
    producto = normalizar_producto(producto)
    return {
        "id": str(producto["_id"]),
        "nombre": producto["nombre"],
        "categoria": producto["categoria"],
        "estado": producto["estado"],
        "precio": producto["precio"],
        "descripcion": producto["descripcion"],
        "imagen": producto["imagen"],
    }


def leer_json():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        abort(400, description="Envía un objeto JSON válido.")
    return leer_formulario(datos)


def carrito_sesion():
    carrito = session.get("carrito", {})
    return carrito if isinstance(carrito, dict) else {}


def obtener_articulos_carrito():
    carrito = carrito_sesion()
    articulos = []
    ids_invalidos = []
    total = 0.0

    for id_producto, cantidad_raw in carrito.items():
        if not ObjectId.is_valid(id_producto):
            ids_invalidos.append(id_producto)
            continue
        producto = productos.find_one({"_id": ObjectId(id_producto)})
        if producto is None or producto.get("estado") != "Disponible":
            ids_invalidos.append(id_producto)
            continue
        try:
            cantidad = int(cantidad_raw)
        except (TypeError, ValueError):
            cantidad = 1
        cantidad = max(1, min(cantidad, CARRITO_MAX_CANTIDAD))
        producto = normalizar_producto(producto)
        precio = producto["precio"]
        subtotal = round(precio * cantidad, 2)
        articulos.append({**producto, "cantidad": cantidad, "subtotal": subtotal})
        total += subtotal

    if ids_invalidos:
        limpio = dict(carrito)
        for id_producto in ids_invalidos:
            limpio.pop(id_producto, None)
        session["carrito"] = limpio

    return articulos, round(total, 2)


@app.template_filter("moneda")
def formato_moneda(valor):
    return f"${precio_seguro(valor):,.2f} MXN"


@app.context_processor
def variables_compartidas():
    try:
        cantidad = sum(max(0, int(v)) for v in carrito_sesion().values())
    except (TypeError, ValueError):
        cantidad = 0
    return {"carrito_cantidad": cantidad}


# --------------------------- TIENDA PARA CLIENTES ---------------------------

@app.route("/")
def tienda():
    lista = [normalizar_producto(p) for p in productos.find({"estado": "Disponible"}).sort("nombre", 1)]
    busqueda = request.args.get("q", "").strip()
    if busqueda:
        termino = busqueda.casefold()
        lista = [
            p for p in lista
            if termino in p.get("nombre", "").casefold()
            or termino in p.get("categoria", "").casefold()
            or termino in p.get("descripcion", "").casefold()
        ]
    return render_template("tienda.html", productos=lista, busqueda=busqueda)


@app.route("/producto/<id>")
def detalle_producto(id):
    producto = buscar_alumno(id)
    relacionados = [
        normalizar_producto(p)
        for p in productos.find({"estado": "Disponible", "categoria": producto.get("categoria")}).limit(4)
        if str(p.get("_id")) != str(producto.get("_id"))
    ]
    return render_template("producto.html", producto=producto, relacionados=relacionados)


@app.route("/carrito")
def ver_carrito():
    articulos, total = obtener_articulos_carrito()
    return render_template("carrito.html", articulos=articulos, total=total)


@app.route("/carrito/agregar/<id>", methods=["POST"])
def agregar_carrito(id):
    producto = buscar_alumno(id)
    if producto.get("estado") != "Disponible":
        flash("Este producto no está disponible actualmente.", "warning")
        return redirect(url_for("detalle_producto", id=id))
    if producto.get("precio", 0) <= 0:
        flash("Antes de comprarlo, el administrador debe asignar un precio al producto.", "warning")
        return redirect(url_for("detalle_producto", id=id))

    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (TypeError, ValueError):
        cantidad = 1
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        flash(f"La cantidad debe estar entre 1 y {CARRITO_MAX_CANTIDAD}.", "warning")
        return redirect(url_for("detalle_producto", id=id))

    carrito = carrito_sesion()
    nueva = int(carrito.get(id, 0)) + cantidad
    if nueva > CARRITO_MAX_CANTIDAD:
        flash(f"Puedes agregar como máximo {CARRITO_MAX_CANTIDAD} unidades de cada producto.", "warning")
        return redirect(url_for("ver_carrito"))
    carrito[id] = nueva
    session["carrito"] = carrito
    flash(f"{producto['nombre']} se agregó al carrito.", "success")
    return redirect(url_for("ver_carrito"))


@app.route("/carrito/actualizar/<id>", methods=["POST"])
def actualizar_carrito(id):
    carrito = carrito_sesion()
    if id not in carrito:
        return redirect(url_for("ver_carrito"))
    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (TypeError, ValueError):
        cantidad = 1
    if cantidad < 1 or cantidad > CARRITO_MAX_CANTIDAD:
        flash(f"La cantidad debe estar entre 1 y {CARRITO_MAX_CANTIDAD}.", "warning")
    else:
        carrito[id] = cantidad
        session["carrito"] = carrito
        flash("Cantidad actualizada.", "success")
    return redirect(url_for("ver_carrito"))


@app.route("/carrito/eliminar/<id>", methods=["POST"])
def eliminar_carrito(id):
    carrito = carrito_sesion()
    carrito.pop(id, None)
    session["carrito"] = carrito
    flash("Producto eliminado del carrito.", "info")
    return redirect(url_for("ver_carrito"))


@app.route("/checkout")
def checkout():
    articulos, total = obtener_articulos_carrito()
    if not articulos:
        flash("Tu carrito está vacío. Agrega un producto antes de continuar.", "info")
        return redirect(url_for("ver_carrito"))
    return render_template("checkout.html", articulos=articulos, total=total)


@app.route("/checkout/confirmar", methods=["POST"])
def confirmar_checkout():
    articulos, total = obtener_articulos_carrito()
    if not articulos:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for("ver_carrito"))

    nombre_cliente = request.form.get("nombre", "").strip()
    correo = request.form.get("correo", "").strip()
    if not nombre_cliente or len(nombre_cliente) > 100:
        flash("Escribe tu nombre (máximo 100 caracteres).", "warning")
        return redirect(url_for("checkout"))
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", correo) or len(correo) > 160:
        flash("Escribe un correo electrónico válido.", "warning")
        return redirect(url_for("checkout"))

    # Checkout demostrativo: no solicita ni procesa tarjetas ni hace cobros reales.
    detalle = [{
        "producto_id": str(item["_id"]),
        "nombre": item["nombre"],
        "categoria": item["categoria"],
        "cantidad": item["cantidad"],
        "precio_unitario": item["precio"],
        "subtotal": item["subtotal"],
    } for item in articulos]
    pedido = {
        "cliente": {"nombre": nombre_cliente, "correo": correo},
        "items": detalle,
        "total": total,
        "metodo_pago": "simulado",
        "estado_pago": "DEMO: no se realizó ningún cobro",
        "creado_en": datetime.now(timezone.utc),
    }
    resultado = pedidos.insert_one(pedido)
    session["carrito"] = {}
    flash("Pedido de demostración creado. No se realizó ningún cobro.", "success")
    return redirect(url_for("confirmacion_compra", id=str(resultado.inserted_id)))


@app.route("/compra/confirmacion/<id>")
def confirmacion_compra(id):
    if not ObjectId.is_valid(id):
        abort(404, description="El número de pedido no es válido.")
    pedido = pedidos.find_one({"_id": ObjectId(id)})
    if pedido is None:
        abort(404, description="El pedido no existe.")
    return render_template("confirmacion.html", pedido=pedido)


# ------------------------------ CRUD DEL ADMIN ------------------------------

@app.route("/admin", endpoint="inicio")
def panel_admin():
    lista = list(alumnos.find().sort("nombre", 1))
    return render_template("index.html", alumnos=[normalizar_producto(p) for p in lista])


@app.route("/agregar", methods=["POST"])
def agregar():
    datos = leer_formulario()
    alumnos.insert_one(datos)
    flash("Producto guardado en el inventario.", "success")
    return redirect(url_for("inicio"))


@app.route("/editar/<id>", methods=["GET", "POST"])
def editar(id):
    alumno = buscar_alumno(id)
    if request.method == "POST":
        datos = leer_formulario()
        alumnos.update_one({"_id": alumno["_id"]}, {"$set": datos})
        flash("Producto actualizado.", "success")
        return redirect(url_for("inicio"))
    return render_template("editar.html", alumno=alumno)


@app.route("/eliminar/<id>", methods=["POST"])
def eliminar(id):
    alumno = buscar_alumno(id)
    alumnos.delete_one({"_id": alumno["_id"]})
    flash("Producto eliminado del inventario.", "info")
    return redirect(url_for("inicio"))


@app.route("/ayuda")
def ayuda():
    fecha = datetime.now().strftime("%d/%m/%Y")
    return render_template("ayuda.html", fecha=fecha)


# -------------------------------- API JSON ---------------------------------

@app.route("/api/productos", methods=["GET", "POST"])
def api_productos():
    if request.method == "POST":
        datos = leer_json()
        resultado = alumnos.insert_one(datos)
        producto = buscar_alumno(str(resultado.inserted_id))
        return jsonify(producto_json(producto)), 201
    lista = alumnos.find().sort("nombre", 1)
    return jsonify([producto_json(p) for p in lista])


@app.route("/api/productos/<id>", methods=["GET", "PUT", "DELETE"])
def api_producto(id):
    producto = buscar_alumno(id)
    if request.method == "PUT":
        datos = leer_json()
        alumnos.update_one({"_id": producto["_id"]}, {"$set": datos})
        return jsonify(producto_json(buscar_alumno(id)))
    if request.method == "DELETE":
        alumnos.delete_one({"_id": producto["_id"]})
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
