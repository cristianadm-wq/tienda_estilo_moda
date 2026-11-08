# Análisis de la actividad y adaptación a Tienda Estilo Moda

## Material revisado

Los dos PDF adjuntos contienen la misma guía de 7 páginas: **Actividad 2: reserva temporal en Dreamy Coffee**. Se revisó el ZIP `proyectos (2)(1).zip` y se trabajó sobre su carpeta `proyectos/belleza-ana/`.

## Qué pide la guía

| PDF | Requisito | Adaptación en Tienda Estilo Moda |
|---|---|---|
| Página 1 | Trabajar sobre el repositorio existente, crear rama y proteger credenciales | Se conserva el proyecto `belleza-ana`; no se crea otra tienda ni se incluye `.env` |
| Página 2 | Stock físico, unidades reservadas, colección `reservas`, duración de 5 minutos, fechas UTC e índices | Se usan `productos`, `stock`, `reservadas`, `reservas`, `reserva_id` y UTC |
| Página 3 | Apartar de forma atómica y liberar reservas vencidas | La actualización condicional del stock y el documento de reserva se ejecutan en transacciones |
| Página 4 | Agregar, consultar y cancelar el carrito desde MongoDB | El carrito se lee de `reservas`; actualizar y eliminar operan sobre la reserva exacta |
| Página 5 | Contador regresivo en el carrito | `carrito.html` muestra el tiempo restante y recarga al vencer |
| Página 6 | Mostrar disponibilidad; actualizar cantidades; confirmar compra; pruebas | La tienda calcula stock libre; checkout descuenta stock y reservas, crea el pedido y elimina las reservas en transacción |
| Página 7 | GitHub, Render y evidencias | Se incluyen comandos de Git y una lista de capturas que debes entregar |

## Diferencias entre la guía y el proyecto

La guía original es para una cafetería y utiliza la colección `alumnos` y el campo `existencias`. Tu proyecto es una tienda de ropa y ya utiliza:

- Colección `productos` en la base de datos `tienda_estilo_moda`.
- Campo `stock` para existencias físicas.
- Variantes de ropa por `talla` y `color`.
- Colecciones `pedidos`, `usuarios`, `cupones` y `resenas`.
- Flask, PyMongo, plantillas Jinja, Bootstrap, GitHub y Render.

Por eso no se cambió el nombre real de las colecciones ni se reemplazó la interfaz. Se conserva el alias `alumnos = productos` que el proyecto ya tenía para compatibilidad con actividades anteriores.

## Hallazgos importantes del ZIP original

La aplicación ya tenía una parte de la lógica de inventario y carrito, pero no estaba integrada de punta a punta con las reservas. El ajuste de cantidad seguía siendo incompatible con el carrito anterior, el checkout necesitaba consumir las reservas para evitar descontar unidades dos veces y el inicio de sesión debía conservar el carrito de invitado. Además, una tienda de ropa debe identificar una línea por producto **y** combinación de talla/color, no solo por el ID del producto.

## Qué incluye esta versión

- Reservas de 5 minutos persistidas en MongoDB, vinculadas a un identificador de sesión.
- Colección `reservas` con un índice único por carrito, producto, talla y color, más un índice por vencimiento.
- Reserva atómica de unidades para bloquear la última pieza incluso cuando dos visitantes intentan comprarla al mismo tiempo.
- Liberación transaccional al cancelar o vencer.
- Carrito, insignia del menú y temporizador basados en los documentos de MongoDB, no en el carrito antiguo de sesión.
- Actualización de cantidades por diferencia: solo aparta o libera las unidades que cambiaron.
- Soporte para varias tallas/colores del mismo producto en el mismo carrito.
- Checkout transaccional: valida reservas activas, descuenta stock físico y reservado, crea el pedido y elimina las reservas.
- Conservación del carrito de invitado al iniciar sesión o registrarse.
- Protección del inventario en el panel administrativo y en la API para no borrar ni reducir unidades ya reservadas.
- Script `migrar_inventario_reservas.py` para agregar los campos faltantes sin sobrescribir el inventario existente.
- Bootstrap local incluido para que los estilos y componentes sigan cargando.

## Archivos principales modificados o añadidos

- `app.py`: reservas, vencimientos, inventario, rutas del carrito, checkout y protección de edición administrativa.
- `templates/carrito.html`: contador y envío de talla/color al actualizar o eliminar cada línea.
- `templates/producto.html`: disponibilidad específica por talla y color.
- `templates/_product_grid.html`: disponibilidad temporal visible en el catálogo.
- `templates/index.html`: existencias reservadas y libres en el panel.
- `templates/ayuda.html`: explicación de las reservas.
- `migrar_inventario_reservas.py`: inicialización única de campos.
- `INSTRUCCIONES_RESERVA_TEMPORAL.md`: instalación, MongoDB, pruebas, evidencias, GitHub y Render.

## Requisito técnico que debes comprobar

Las transacciones de MongoDB necesitan **MongoDB Atlas compatible** o un MongoDB local configurado como *replica set*. Un servicio local que funcione como `standalone` no admite `start_transaction()`. La guía incluida explica cómo configurar `rs0` sin reemplazar el resto de la configuración de `mongod.cfg`.

## Verificación realizada aquí

- Los archivos Python compilan con `py_compile`.
- Las 20 plantillas HTML/Jinja se pudieron cargar y analizar.
- Se comprobó que los archivos locales de Bootstrap estén presentes.
- No se pudo ejecutar una prueba real contra tu MongoDB Atlas/servidor local desde este entorno. Por eso las pruebas de reserva, concurrencia, vencimiento y compra deben ejecutarse en tu computadora con tu `.env` y tu base de datos.

## Orden recomendado para ti

1. Haz una copia de seguridad de tu carpeta actual.
2. Copia el contenido del ZIP nuevo dentro de `belleza-ana`, sin reemplazar `.env`, `.git` ni `.venv`.
3. Comprueba que MongoDB sea Atlas o un replica set local.
4. Ejecuta una sola vez `migrar_inventario_reservas.py`.
5. Inicia Flask y ejecuta las pruebas descritas en `INSTRUCCIONES_RESERVA_TEMPORAL.md`.
6. Solo después de aprobar las pruebas, crea el commit y despliega a Render.
