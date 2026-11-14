# Tienda Estilo Moda — Proyecto final: reserva temporal y código QR

Aplicación Flask + MongoDB Atlas + Bootstrap para una tienda de sneakers y streetwear. Esta versión conserva el CRUD, las cuentas, el catálogo y los pedidos del proyecto original, e integra reservas temporales de inventario y fichas públicas mediante QR.

## Funciones principales

- Registro e inicio de sesión con contraseñas cifradas mediante hash y roles de cliente/administrador.
- Catálogo con búsqueda, filtros, ordenamiento, favoritos, drops y etiquetas de NUEVO, SALE y AGOTADO.
- Inventario simple o por combinación de talla y color.
- Productos agotados visibles, pero no comprables.
- Carrito persistido en MongoDB Atlas como reservas temporales de cinco minutos.
- Contador visible basado en la fecha `vence_en` guardada en Atlas.
- Bloqueo atómico del inventario: dos visitantes no pueden reservar las mismas últimas unidades.
- Historial de reservas con estados `temporal`, `confirmada`, `cancelada` y `vencida`.
- Cancelación y liberación al vencer, mediante transacciones de MongoDB.
- Checkout demostrativo: confirma la reserva, descuenta inventario, crea el pedido y conserva el estado de la reserva. No procesa pagos reales.
- QR descargable por producto que abre una ficha pública y actualizada en `/ficha/<id>`.
- Dashboard administrativo, pedidos, cupones, reseñas y API JSON.

## Base de datos

Base: `tienda_estilo_moda`.

Colecciones utilizadas: `productos`, `usuarios`, `pedidos`, `cupones`, `resenas` y `reservas`.

Para inventario simple se usan `stock` y `reservadas`. Para variantes se usan `variantes[].stock` y `variantes[].reservadas`. La disponibilidad se calcula restando unidades reservadas a las existencias físicas.

Cada documento de `reservas` contiene, entre otros datos: `reserva_id`, `visitante_id`, `recurso_id`, `producto_id`, `cantidad`, `talla`, `color`, `estado`, `creada_en` y `vence_en`.

## Requisitos

- Python 3.12 (ver `.python-version`).
- MongoDB Atlas, o MongoDB local configurado como replica set. Las reservas usan transacciones multi-documento; un MongoDB local standalone no es suficiente.
- Variables de entorno `MONGO_URI` y `SECRET_KEY`.

## Instalación local (Windows PowerShell)

1. Conserva una copia de seguridad de tu carpeta y no reemplaces `.git`, `.env` ni `.venv`.
2. Crea `.env` desde `.env.example` si todavía no existe y coloca tu URI de Atlas y una clave secreta privada.
3. En la carpeta del proyecto:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe validar_mongo.py
   .\.venv\Scripts\python.exe migrar_inventario_reservas.py
   .\.venv\Scripts\python.exe app.py
   ```

4. Abre `http://127.0.0.1:5000/`.
5. Revisa el inventario en Atlas y corrige cualquier valor temporal. La migración usa 10 solo como valor provisional para productos antiguos disponibles que no tenían `stock`; no representa el inventario real.

## Variables de entorno

Ejemplo de `.env`:

```env
MONGO_URI=mongodb+srv://USUARIO:CONTRASENA@CLUSTER.mongodb.net/?retryWrites=true&w=majority
SECRET_KEY=GENERA_UNA_CLAVE_LARGA_Y_ALEATORIA
APP_BASE_URL=
```

No subas `.env` a GitHub ni compartas `MONGO_URI` o `SECRET_KEY`. En producción, configura `APP_BASE_URL` con la URL HTTPS real de Render para que el QR no codifique `localhost`.

## Rutas importantes

- `/` — tienda y catálogo.
- `/carrito` — carrito con contador de reserva.
- `/checkout` — checkout de demostración.
- `/producto/<id>` — detalle de producto con QR.
- `/ficha/<id>` — ficha pública a la que dirige el QR.
- `/qr/producto/<id>.png` — imagen PNG del QR.
- `/api/salud` — comprobación de conexión con MongoDB.
- `/api/catalogo` y `/api/productos` — API JSON.

## Render

Usa el servicio existente conectado al repositorio `cristianadm-wq/tienda_estilo_moda`. Configuración habitual:

- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`
- Variables: `MONGO_URI`, `SECRET_KEY`, `APP_BASE_URL=https://tienda-estilo-moda.onrender.com`.

Conserva la lista de acceso de Atlas y autoriza las direcciones de salida que Render muestre para el servicio. No uses `0.0.0.0/0` como solución permanente.

## Documentación de entrega

Consulta `GUIA_PROYECTO_FINAL.pdf` (guía paso a paso), `GUIA_PROYECTO_FINAL.md` e `INSTRUCCIONES_RESERVA_TEMPORAL.md`. `PLANTILLA_INFORME_CLASSROOM.docx` es una plantilla editable de siete páginas; `PLANTILLA_INFORME_CLASSROOM.pdf` permite revisar su formato. Reemplaza los recuadros con capturas reales y exporta el DOCX a PDF para Classroom.

## Pago

El checkout sigue siendo de demostración. No solicita ni procesa datos bancarios y no realiza cobros reales.
