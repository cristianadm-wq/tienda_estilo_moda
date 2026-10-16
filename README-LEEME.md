# Tienda Estilo Moda — Flask + MongoDB + Bootstrap

Esta carpeta contiene una actualización del proyecto que compartiste. Incluye la corrección del menú HTML, el panel CRUD de inventario, la tienda pública, la ficha de producto, el carrito de sesión, un checkout demostrativo y los endpoints JSON descritos en la Práctica 05.

## Cómo aplicar esta actualización a tu carpeta actual

1. Detén Flask si está ejecutándose (en su terminal, `Ctrl + C`).
2. Haz una copia de seguridad de tu carpeta actual `belleza-ana`.
3. Extrae los archivos de este ZIP dentro de tu carpeta actual `belleza-ana` y acepta reemplazar los archivos con el mismo nombre.
4. No borres la carpeta `.git` ni tu `.venv`; no se incluyen en este ZIP.
5. Comprueba que MongoDB local esté iniciado.
6. En una terminal de VS Code dentro de `belleza-ana`, ejecuta:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe app.py
   ```

7. Abre las direcciones indicadas abajo.

## Rutas principales

- `/` — catálogo de la tienda.
- `/producto/ID` — detalle de un producto.
- `/carrito` — revisar el carrito.
- `/carrito/agregar/ID` — agregar por formulario POST.
- `/checkout` — formulario de pedido de demostración.
- `/admin` — inventario: agregar, editar y eliminar productos.
- `/ayuda` — ayuda.
- `/api/productos` — API JSON para listar y crear.
- `/api/productos/ID` — API JSON para consultar, editar y eliminar.

Primero agrega productos desde `/admin`. Para que se puedan agregar al carrito deben tener estado `Disponible` y precio mayor a cero. Las imágenes son opcionales; si no agregas una URL, se usa un marcador local.

## Render

Antes de publicar, configura estas variables en el nuevo servicio de Render:

- `MONGO_URI`: URI de MongoDB Atlas con usuario/credenciales del proyecto nuevo.
- `SECRET_KEY`: una clave aleatoria larga, exclusiva de este servicio.

Comando de construcción: `pip install -r requirements.txt`

Comando de inicio: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`

No subas `.env`, contraseñas ni la carpeta `.venv`.

## Importante

El checkout solo guarda un pedido de prueba en la colección `pedidos`. No cobra, no procesa tarjetas y no debe utilizarse para ventas reales. El panel `/admin` y la API no tienen inicio de sesión; no publiques datos reales ni uses la aplicación como tienda comercial real sin añadir autenticación, autorización y protección adicional.
