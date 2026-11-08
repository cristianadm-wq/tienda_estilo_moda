# Reserva temporal de inventario — Tienda Estilo Moda

Esta adaptación implementa en el proyecto existente los requisitos de la actividad de reserva temporal. No crea una tienda nueva ni sustituye la interfaz de Tienda Estilo Moda.

## Qué quedó integrado

- Cada producto puede reservarse durante **5 minutos**.
- El carrito real se consulta desde MongoDB, colección `reservas`; dejó de depender de `session['carrito']`.
- Los productos simples usan `stock` y `reservadas`.
- Los productos con tallas y colores usan `variantes[].stock` y `variantes[].reservadas`.
- La disponibilidad visible se calcula como existencias físicas menos unidades reservadas.
- La reserva, cancelación, vencimiento, ajuste de cantidad y confirmación de pedido actualizan inventario y reserva mediante transacciones.
- Al cancelar o vencer, las unidades vuelven a estar disponibles.
- Al confirmar, se descuentan tanto `stock` como `reservadas`, se crea el pedido y se eliminan los documentos de reserva.
- El inicio de sesión y el registro conservan el identificador del carrito invitado, para que no se pierdan las reservas al entrar al checkout.
- El panel administrativo no permite eliminar productos ni quitar variantes que tengan reservas activas, ni reducir las existencias por debajo de las unidades apartadas.

## Adaptación de nombres respecto al PDF

La guía de Dreamy Coffee usa la colección `alumnos` y los campos `existencias`. Este proyecto de moda usa la colección `productos` y el campo `stock`, por eso se conservaron los nombres reales del proyecto. También se conserva el alias `alumnos = productos` por compatibilidad con las prácticas anteriores.

Ejemplo de producto sin variantes:

```json
{
  "nombre": "Sudadera negra",
  "stock": 3,
  "reservadas": 1,
  "estado": "Disponible"
}
```

Ejemplo de producto por talla y color:

```json
{
  "nombre": "Tenis urbanos",
  "variantes": [
    { "talla": "26", "color": "Blanco", "stock": 3, "reservadas": 1 },
    { "talla": "27", "color": "Blanco", "stock": 2, "reservadas": 0 }
  ]
}
```

La colección `reservas` almacena un documento por carrito, producto y variante, con campos como `reserva_id`, `producto_id`, `cantidad`, `talla`, `color`, `estado`, `creada_en` y `vence_en`. El índice único utiliza `reserva_id + producto_id + talla + color`, por lo que un mismo carrito puede reservar varias combinaciones de talla/color del mismo producto. En productos sin variantes, talla y color se guardan como cadenas vacías.

## Requisito de MongoDB para transacciones

**Importante:** `start_transaction()` necesita MongoDB Atlas o un servidor MongoDB configurado como replica set. Una instalación local estándar que funcione como servidor independiente (*standalone*) no admite estas transacciones.

- Si el proyecto ya usa MongoDB Atlas, conserva tu `MONGO_URI` actual en `.env`; Atlas admite transacciones en los clústeres compatibles.
- Si usas MongoDB local, debes configurarlo como replica set. En `mongod.cfg`, agrega al nivel principal (sin meterlo dentro de `storage`):

```yaml
replication:
  replSetName: rs0
```

Reinicia el servicio de MongoDB y abre `mongosh`:

```javascript
rs.initiate()
rs.status()
```

Después, el URI local de `.env` debe incluir el replica set:

```env
MONGO_URI=mongodb://127.0.0.1:27017/?replicaSet=rs0
```

No configures esto a ciegas sobre un `mongod.cfg` distinto: conserva sus valores actuales de `storage.dbPath`, `systemLog` y `net.port`.

## Instalación en tu proyecto

1. Detén Flask con `Ctrl + C`.
2. Haz una copia completa de tu carpeta `belleza-ana` antes de reemplazar archivos.
3. Copia los archivos de este paquete sobre tu carpeta del proyecto y acepta reemplazar los archivos que tengan el mismo nombre.
4. **No borres ni reemplaces** tu archivo `.env`, la carpeta `.git` ni tu entorno `.venv`.
5. Comprueba que `.env` tenga `MONGO_URI` y `SECRET_KEY`. No pegues esas claves en el código ni las subas a GitHub.
6. Antes de cambiar el inventario, abre MongoDB Atlas/Compass y toma una captura de un producto de prueba donde se vean sus campos actuales. Esa evidencia previa también la pide la guía.
7. Ejecuta una sola vez la migración:

```powershell
.\.venv\Scripts\python.exe migrar_inventario_reservas.py
```

8. Revisa los productos en MongoDB Atlas/Compass, colección `tienda_estilo_moda.productos`. Corrige las existencias de prueba y coloca el stock real. El número `10` solo se usa para productos antiguos sin campo `stock` y debe sustituirse por las existencias reales.
9. Instala dependencias y ejecuta la tienda:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

10. Abre `http://127.0.0.1:5000/`.

Al iniciar la aplicación se crean los índices de `reservas`: uno único para `reserva_id + producto_id + talla + color` y otro para `vence_en`. Si existe el índice único anterior de `reserva_id + producto_id`, se sustituye por el índice compatible con variantes. Los documentos antiguos de productos simples reciben talla y color vacíos para conservar su identidad.

## Pruebas obligatorias

Hazlas primero con un producto de prueba, no con toda la tienda.

1. **Reserva normal:** deja un producto con 3 existencias y 0 reservadas. Agrégalo una vez. El carrito debe mostrar cantidad 1 y MongoDB debe mostrar `reservadas: 1` (o el contador equivalente en la variante).
2. **Última unidad:** deja un producto con 1 existencia. En una ventana normal, resérvalo. En una ventana incógnita, abre el mismo producto. Debe aparecer `Sin disponibilidad temporal` y no debe permitir reservarlo.
3. **Cancelación:** elimina el artículo del carrito. El documento de reserva debe borrarse y el contador de reservadas debe volver a 0.
4. **Vencimiento:** vuelve a reservar y espera 5 minutos. Al recargar el catálogo o carrito, la reserva vencida se libera y la unidad vuelve a estar disponible.
5. **Compra:** inicia sesión o crea una cuenta, reserva un producto y confirma el pedido. Debe aparecer un documento en `pedidos`, disminuir `stock`, disminuir `reservadas` y desaparecer el documento de `reservas`. El pago sigue siendo una simulación.
6. **Varias tallas/colores:** reserva dos combinaciones distintas del mismo producto en el mismo carrito. Deben aparecer como dos líneas, y actualizar/eliminar una no debe modificar la otra.
7. **Doble clic / concurrencia:** prueba dos ventanas con la última unidad. Nunca deben quedar existencias disponibles ni contadores reservados negativos.

## Evidencias para entregar

- Captura de un producto en Atlas con existencias y reservadas.
- Captura de un documento activo en la colección `reservas`.
- Captura del contador de 5 minutos en el carrito.
- Captura de la ventana A que reservó y ventana B que ya no puede reservar la última unidad.
- Captura después de cancelar o vencer, mostrando que la unidad se liberó.
- Captura de la compra confirmada y del inventario actualizado.
- Enlace del commit o rama en GitHub.
- URL funcional de Render.

## GitHub y Render

En la carpeta `belleza-ana`, revisa primero la rama y los cambios:

```powershell
git status
git branch --show-current
```

Si sigues en la rama `reserva-temporal`, después de probar localmente:

```powershell
git add app.py migrar_inventario_reservas.py INSTRUCCIONES_RESERVA_TEMPORAL.md templates static
git commit -m "Completar reservas temporales de inventario"
git push -u origin reserva-temporal
```

Verifica que Render despliegue la rama correcta o integra la rama a `main` según las indicaciones de tu docente. En Render deben seguir configuradas las variables secretas `MONGO_URI` y `SECRET_KEY`. Después prueba la URL pública en una ventana normal y otra incógnita.

## Nota de seguridad

El checkout continúa siendo de demostración. No solicita datos bancarios ni realiza cobros reales.
