# Tienda Estilo Moda — Flask + MongoDB + Bootstrap

Actualización funcional del proyecto Tienda Estilo Moda. Conserva las colecciones `productos`, `pedidos` y `usuarios` de `tienda_estilo_moda`, y añade las colecciones `cupones` y `resenas` cuando se utilizan esas funciones.

## Funciones incluidas

- Registro, inicio y cierre de sesión; contraseñas con hash y rol de administrador.
- Catálogo con búsqueda del lado del servidor al escribir, filtros por categoría/estado/precio y ordenamiento.
- Productos agotados visibles pero no comprables.
- Favoritos guardados por usuario.
- Variantes por talla/color con stock, validación del inventario y descuento de existencias al crear un pedido de demostración.
- Etiquetas NUEVO/SALE/AGOTADO y lanzamientos con cuenta regresiva.
- Carrito con múltiples combinaciones de talla/color.
- Checkout simulado: no procesa cobros ni recopila información de tarjetas.
- Historial de pedidos para clientes y gestión del estado de pedido para administración.
- Cupones de porcentaje/cantidad fija, vigencia opcional, compra mínima y límite de usos.
- Reseñas de compradores, puntuación y moderación administrativa.
- Dashboard de inventario, usuarios, pedidos, stock bajo y ventas simuladas.
- API JSON pública de lectura; crear/editar/eliminar vía API requiere una sesión de administrador.

## Instalar en tu copia local

1. Detén Flask con `Ctrl+C`.
2. Haz una copia de seguridad de la carpeta `belleza-ana`.
3. Extrae los archivos del paquete dentro de `belleza-ana` y reemplaza los archivos de igual nombre. **No reemplaces ni borres `.git`, `.venv` o `.env`.**
4. Comprueba que el archivo `.env` local conserva tu `MONGO_URI` y `SECRET_KEY`. No uses los valores de ejemplo como secretos reales.
5. En la terminal de VS Code, dentro de `belleza-ana`, ejecuta:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe app.py
   ```

6. Abre `http://127.0.0.1:5000/`.

La cuenta administradora existente se conserva si el usuario en MongoDB tiene `rol: "admin"`. El registro de nuevas cuentas será como cliente cuando ya exista un administrador.

## Variantes de inventario

En el panel de administración, introduce una combinación por línea con este formato:

```text
26 | Blanco | 3
27 | Blanco | 2
26 | Negro | 4
```

Los números al final son las unidades disponibles. Si no usas variantes, configura las existencias simples en el formulario.

## Cupones y drops

En `/admin/cupones`, crea códigos. El tipo porcentaje admite valores de 1 a 100; el tipo cantidad fija se expresa en MXN. `Límite total de usos = 0` significa sin límite. La fecha del drop y las fechas de cupones se interpretan como UTC.

## Prueba opcional de la API

En PowerShell configura las variables de sesión (no las escribas dentro del código):

```powershell
$env:TSM_ADMIN_EMAIL="correo-de-tu-cuenta-admin"
$env:TSM_ADMIN_PASSWORD="tu-contraseña-admin"
.\.venv\Scripts\python.exe probar_api.py
```

El script crea un producto temporal, lo consulta, lo actualiza y lo elimina.

## Render

Publica los cambios solo después de probar en local. En Render deben permanecer configuradas las variables secretas `MONGO_URI` y `SECRET_KEY`; no subas `.env` ni contraseñas a GitHub. Build: `pip install -r requirements.txt`. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`.

## Límites

La parte de pago, la transferencia y el pago contra entrega son demostraciones académicas. No se realizan cobros. Antes de utilizar una aplicación para ventas reales harían falta, como mínimo, un proveedor de pagos, protección adicional de producción, pruebas de concurrencia y controles operativos.
