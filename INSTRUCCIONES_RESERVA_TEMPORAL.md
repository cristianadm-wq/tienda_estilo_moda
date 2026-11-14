# Reserva temporal y QR — Tienda Estilo Moda

Esta guía adapta el proyecto final del CBTIS 272 a una tienda de sneakers y streetwear. No crea una aplicación nueva ni sustituye la tienda original.

## Modelo de reserva elegido

El recurso es **una cantidad de un producto**, identificada además por talla y color cuando el producto tiene variantes. La reserva dura **5 minutos**.

- `stock`: existencias físicas totales.
- `reservadas`: unidades apartadas temporalmente.
- `stock_disponible`: cálculo de presentación (`stock - reservadas`).
- Para variantes, el inventario y las reservas se guardan por cada combinación de talla/color.

La colección `reservas` conserva el historial. Sus estados son:

- `temporal`: unidad apartada y todavía vigente.
- `confirmada`: forma parte de un pedido.
- `cancelada`: el visitante liberó la unidad.
- `vencida`: terminó el plazo y se liberó la unidad.

## Seguridad y requisitos de MongoDB

Las reservas y la confirmación de pedidos usan transacciones multi-documento. Utiliza MongoDB Atlas (replica set administrado) o un MongoDB local configurado como replica set. Un servicio MongoDB local standalone no admite estas transacciones.

Nunca pegues `MONGO_URI`, `SECRET_KEY`, contraseñas o claves de administración en `app.py`, GitHub, capturas ni el informe.

## Instalación y actualización local

1. Detén Flask con `Ctrl + C`.
2. Haz copia de seguridad de toda la carpeta `belleza-ana`.
3. Copia los archivos de este paquete sobre tu carpeta existente y acepta reemplazar los archivos del proyecto.
4. No reemplaces ni borres `.git`, `.env` ni `.venv`.
5. Comprueba que `.env` contiene la URI correcta de MongoDB Atlas y una `SECRET_KEY` privada. `APP_BASE_URL` puede quedar vacío en local.
6. Instala dependencias:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

7. Comprueba la conexión:

   ```powershell
   .\.venv\Scripts\python.exe validar_mongo.py
   ```

8. Ejecuta la migración **una sola vez antes de probar reservas**:

   ```powershell
   .\.venv\Scripts\python.exe migrar_inventario_reservas.py
   ```

9. Abre Atlas → `tienda_estilo_moda` → `productos`. Revisa `stock` y `reservadas`. En productos antiguos sin stock, el script inicializa 10 unidades provisionales cuando el estado es Disponible. Corrige ese valor desde Administración al inventario real antes de publicar. Para variantes, revisa `variantes[].stock` y `variantes[].reservadas`.
10. Inicia la tienda:

   ```powershell
   .\.venv\Scripts\python.exe app.py
   ```

11. Abre `http://127.0.0.1:5000/`.

La colección `reservas` y sus índices se crean al iniciar la aplicación. Las reservas activas antiguas con estado `activa` se migran a `temporal`; las canceladas, vencidas y confirmadas se conservan para historial.

## Flujo del cliente

1. El cliente elige un producto y, si corresponde, talla y color.
2. Flask consulta y actualiza el inventario en Atlas dentro de una transacción.
3. Si hay unidades, crea una reserva y registra `creada_en` y `vence_en`.
4. El carrito muestra un contador calculado con `vence_en`.
5. El cliente puede actualizar la cantidad, cancelar o confirmar.
6. Cancelar cambia el estado a `cancelada` y devuelve las unidades reservadas.
7. Al vencer, la aplicación cambia el estado a `vencida` y devuelve las unidades al primer acceso que comprueba el inventario después del vencimiento.
8. Confirmar cambia el estado a `confirmada`, crea el pedido, descuenta `stock` y `reservadas` y limpia la sesión de reserva.

MongoDB y Flask son la autoridad del tiempo y la disponibilidad; el contador del navegador solo informa. Las consultas de catálogo, carrito y ficha liberan las reservas vencidas antes de mostrar la disponibilidad.

## Código QR

En el detalle de cada producto aparece un QR y un botón para abrir o descargar su ficha. El QR apunta a `/ficha/<id>`, que consulta Atlas cada vez que se abre y muestra datos públicos actualizados: nombre, categoría, descripción, precio y disponibilidad. No expone cuentas, correos, claves, contraseñas ni `MONGO_URI`.

En Render configura `APP_BASE_URL` con la URL HTTPS del servicio, por ejemplo:

```text
APP_BASE_URL=https://tienda-estilo-moda.onrender.com
```

Después de publicar, abre el detalle de producto, escanea el QR con un celular y confirma que abre la ficha de Render. Cambia temporalmente el stock/estado desde Administración y escanea el mismo QR otra vez: la ficha debe mostrar la información actualizada.

## Pruebas obligatorias

Utiliza un producto de prueba con al menos una unidad disponible. Para demostrar el bloqueo, utiliza una ventana normal (usuario A) y otra de incógnito (usuario B).

1. A reserva una unidad disponible: el carrito muestra el contador y Atlas registra `estado: temporal`, `cantidad`, `creada_en` y `vence_en`.
2. B abre el mismo producto y actualiza la página: ve disponibilidad temporal agotada y no puede reservar esa última unidad.
3. A cancela: Atlas conserva una reserva `cancelada` y las unidades reservadas regresan a cero; B ya puede reservar.
4. A reserva y espera 5 minutos: al recargar catálogo o carrito, Atlas registra `vencida` y libera las unidades.
5. A reserva de nuevo y confirma: se crea un pedido, el stock físico disminuye, el campo reservado disminuye y la reserva queda `confirmada` con el `pedido_id`.
6. Recarga o vuelve a abrir la página: la disponibilidad sigue coincidiendo con Atlas.
7. Escanea el QR desde un celular: abre `/ficha/<id>` en Render.
8. Cambia la disponibilidad desde Administración y escanea el mismo QR: muestra el nuevo estado.
9. Abre `/ficha/ID-QUE-NO-EXISTE`: debe mostrar un error 404 entendible y no detener el servicio.
10. Abre el sitio desde otro equipo o celular: no debe depender de `localhost` ni de archivos locales.

No afirmes que una prueba pasó hasta observar el resultado real en tu sitio y en Atlas. Conserva capturas antes y después.

## GitHub

Trabaja en la rama que contiene la mejora (por ejemplo `proyecto-final` o `reserva-temporal`, según la que ya creaste). Antes de subir:

```powershell
git status
git branch --show-current
git check-ignore -v .env
git add app.py requirements.txt .env.example README.md GUIA_PROYECTO_FINAL.md INSTRUCCIONES_RESERVA_TEMPORAL.md migrar_inventario_reservas.py validar_mongo.py templates static
git status
git commit -m "Integra reservas temporales y QR en la tienda"
git push origin NOMBRE-DE-TU-RAMA
```

Sustituye `NOMBRE-DE-TU-RAMA` por el nombre que devuelva `git branch --show-current`. Antes de `commit`, comprueba que `.env` no aparece entre los archivos preparados. El PDF solicita al menos cuatro commits en el historial; conserva los commits previos del primer parcial y documenta los avances reales sin crear commits vacíos.

## Render

Actualiza el servicio existente de Tienda Estilo Moda, no crees otra tienda:

- Repositorio: `cristianadm-wq/tienda_estilo_moda`.
- Build: `pip install -r requirements.txt`.
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`.
- Variables: `MONGO_URI`, `SECRET_KEY`, `APP_BASE_URL`.
- `APP_BASE_URL` debe ser la URL HTTPS del servicio de Render.

Asegúrate de que Render despliegue la rama donde subiste los cambios. En MongoDB Atlas → Network Access, conserva las IP que necesites y permite las direcciones de salida indicadas por Render. No publiques la URI ni abras el acceso global de forma permanente.

Al terminar, prueba `/api/salud`, la tienda, reserva, cancelación, vencimiento, compra y QR desde la URL pública.

## Evidencias que debes reunir

- Producto y sus existencias antes de la migración.
- Documento del producto en Atlas con `stock` y `reservadas`.
- Colección `reservas` mostrando un documento temporal con `vence_en`.
- Carrito con contador de 5 minutos.
- Usuario A reservando y usuario B bloqueado.
- Reserva cancelada y disponibilidad liberada.
- Reserva vencida y disponibilidad liberada.
- Pedido confirmado y reserva con estado `confirmada`.
- QR en el detalle del producto y escaneo desde celular.
- Ficha pública mostrando un estado actualizado.
- GitHub con README, historial de commits y Render Live.

Captura Atlas sin mostrar usuarios/contraseñas ni la cadena `MONGO_URI`.
