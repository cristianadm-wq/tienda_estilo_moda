# Guía de instalación, pruebas y entrega
## Proyecto final: Tienda Estilo Moda — Reserva temporal y código QR

**Plantel:** CBTIS 272, Sor Juana Inés de la Cruz  
**Materia:** Aplicaciones Web · 5.º semestre  
**Grupo:** 5APV  
**Integrantes indicados:** Cristian Alejandro Díaz Mendoza y Jorge Pérez Pool  
**Proyecto:** Tienda Estilo Moda

> Esta guía acompaña al ZIP actualizado. No sustituye las pruebas reales: completa la tabla de resultados y agrega las capturas de tu aplicación y de MongoDB Atlas antes de entregar.

---

# A. Qué se agregó al proyecto

El proyecto original sigue siendo una tienda de sneakers y streetwear. No se cambió a cafetería ni se creó otra aplicación.

La mejora reserva **una cantidad de un producto durante cinco minutos**. Si el artículo tiene tallas y colores, la reserva se identifica por producto + talla + color. El segundo visitante no puede apartar las mismas últimas unidades.

También se agregó un código QR por producto. Al escanearlo abre una ficha pública de la tienda, que consulta los datos actuales en MongoDB Atlas.

## Estados que se guardan en Atlas

| Estado | Significado |
|---|---|
| `temporal` | El visitante tiene las unidades apartadas y todavía no vence el tiempo. |
| `confirmada` | Las unidades quedaron asociadas a un pedido. |
| `cancelada` | El visitante canceló y se liberaron las unidades. |
| `vencida` | Terminó el tiempo y se liberaron las unidades. |

La colección `reservas` conserva los registros históricos. El contador del navegador no decide la disponibilidad: Flask y MongoDB la comprueban en cada operación.

---

# B. Antes de instalar

Necesitas:

- Windows y Visual Studio Code.
- Python 3.12, indicado en `.python-version`.
- La carpeta original `belleza-ana` y su entorno `.venv`.
- La conexión existente a MongoDB Atlas.
- Acceso al repositorio GitHub `cristianadm-wq/tienda_estilo_moda`.
- Acceso al servicio Render de Tienda Estilo Moda.

**No combines este proyecto con `proyecto_qr_flask_mongodb` ni con `proyecto_flask_reserva_temporal`.** Esta mejora pertenece a la tienda original.

---

# C. Instalar el ZIP actualizado sin perder tu tienda

1. Descarga `Tienda_Estilo_Moda_Proyecto_Final.zip` y extráelo.
2. Antes de copiar archivos, detén Flask en VS Code con `Ctrl + C`.
3. Haz una copia de seguridad completa de tu carpeta `belleza-ana`.
4. Abre la carpeta extraída del paquete. Dentro encontrarás `app.py`, `templates`, `static`, `requirements.txt` y los scripts de migración.
5. Copia el contenido de esa carpeta sobre la carpeta existente `belleza-ana` y acepta reemplazar los archivos con el mismo nombre.
6. **No reemplaces ni borres** `.git`, `.venv` ni `.env`. Esos tres elementos pertenecen a tu entorno local y tu historial.
7. Comprueba que `.env` siga existiendo y contenga tu `MONGO_URI` y tu `SECRET_KEY`. No la compartas ni la subas a GitHub.
8. Abre la terminal de VS Code. Debe estar en `belleza-ana` y mostrar el entorno `(.venv)`.

Comprueba la ubicación:

```powershell
Get-Location
Test-Path .\app.py
Test-Path .\requirements.txt
Test-Path .env
```

Los tres `Test-Path` deben devolver `True`.

---

# D. Instalar dependencias y comprobar Atlas

En la terminal, dentro de `belleza-ana`, ejecuta:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Esto instalará también `qrcode[pil]`, necesaria para generar las imágenes QR.

Después ejecuta:

```powershell
.\.venv\Scripts\python.exe validar_mongo.py
```

El resultado debe indicar conexión correcta y soporte de transacciones. Si muestra que no admite transacciones, revisa que `MONGO_URI` apunte a MongoDB Atlas o a un replica set; una instancia local standalone no es suficiente.

**No continúes con las reservas si Atlas no conecta.** Revisa URI, usuario, contraseña y Network Access. No pegues credenciales en el chat.

---

# E. Migrar el inventario una vez

Antes de la primera reserva, hay que asegurar que los productos tengan contadores de unidades reservadas.

Ejecuta una sola vez:

```powershell
.\.venv\Scripts\python.exe migrar_inventario_reservas.py
```

El script:

- No reemplaza los valores `stock` que ya existen.
- Añade `reservadas: 0` cuando falta el campo.
- Añade `reservadas: 0` a las variantes que todavía no lo tengan.
- Para productos antiguos sin campo `stock`, coloca 10 como cantidad provisional únicamente si el estado es Disponible.

Después abre MongoDB Atlas → `tienda_estilo_moda` → `productos` y revisa el inventario. **Cambia cualquier valor provisional por la cantidad real** desde el panel de Administración antes de publicar.

La colección `reservas` y sus índices se crean al iniciar la aplicación. Las reservas antiguas con estado `activa` se migran a `temporal`; las nuevas conservan estados históricos.

---

# F. Ejecutar y probar localmente

Inicia Flask:

```powershell
.\.venv\Scripts\python.exe app.py
```

Abre en Chrome:

- Tienda: `http://127.0.0.1:5000/`
- Salud de MongoDB: `http://127.0.0.1:5000/api/salud`

La ruta de salud debe devolver `ok: true`. No cierres la terminal mientras haces las pruebas.

## Preparación del producto de prueba

1. Entra al panel de Administración.
2. Usa un producto ficticio o crea uno solo para la prueba.
3. Coloca precio válido y una existencia controlada, por ejemplo `1` unidad.
4. Si usas tallas y colores, crea una sola combinación de prueba con `stock: 1`.
5. Guarda y comprueba en Atlas que `stock` sea 1 y `reservadas` sea 0.

No hagas estas pruebas con toda la mercancía real del catálogo.

## Diez pruebas del PDF

Hazlas con una ventana normal (Usuario A) y otra ventana de incógnito (Usuario B). Usa el mismo producto de prueba con una sola unidad.

| N.º | Qué hacer | Resultado esperado | Captura |
|---:|---|---|---|
| 1 | A reserva una unidad disponible. | El carrito muestra 05:00 aprox. y Atlas guarda una reserva `temporal` con `vence_en`. | 1 |
| 2 | B abre el mismo producto y recarga. | La unidad aparece sin disponibilidad temporal; B no puede apartarla. | 2 |
| 3 | A cancela desde el carrito. | Atlas conserva la reserva como `cancelada`; `reservadas` vuelve a 0. B puede reservar. | 3 |
| 4 | A vuelve a reservar y espera cinco minutos. | Al volver a consultar la tienda/carrito, Atlas muestra `vencida` y libera la unidad. | 4 |
| 5 | A reserva otra vez e inicia sesión para confirmar el checkout. | Se crea un pedido, se descuenta `stock`, se libera `reservadas` y la reserva queda `confirmada`. | 5 |
| 6 | Recarga o vuelve a abrir la tienda. | La disponibilidad coincide con MongoDB Atlas. | 6 |
| 7 | Abre el detalle del producto desde un celular y escanea el QR. | Se abre la ficha pública en la URL de Render. | 7 |
| 8 | Cambia stock/estado desde Administración y escanea el mismo QR. | La ficha muestra la disponibilidad nueva; no hace falta generar otro QR. | 8 |
| 9 | Abre una ficha con un ID inexistente, por ejemplo `/ficha/ID-INVALIDO`. | Se muestra un 404 entendible; la aplicación sigue funcionando. | 9 |
| 10 | Abre la página desde otro teléfono/equipo. | La tienda y el QR funcionan por la URL pública, no por localhost. | 10 |

**La prueba 4 es importante:** el código libera las reservas vencidas al siguiente acceso que consulta la disponibilidad (catálogo, carrito, ficha o API de catálogo). Comprueba el cambio en Atlas al recargar.

## Dónde observar los datos en Atlas

- Base: `tienda_estilo_moda`.
- Productos: `productos` (`stock`, `reservadas`, `variantes`).
- Reservas: `reservas` (`reserva_id`, `visitante_id`, `recurso_id`, `producto_id`, `cantidad`, `estado`, `creada_en`, `vence_en`, y fechas de cancelación/vencimiento/confirmación).
- Pedidos: `pedidos`.

---

# G. Comprobar el QR

En el detalle de cada producto verás un bloque “Escanea el código QR”. La imagen se genera en:

```text
/qr/producto/ID_DEL_PRODUCTO.png
```

El QR contiene la dirección pública:

```text
https://tienda-estilo-moda.onrender.com/ficha/ID_DEL_PRODUCTO
```

La ruta `/ficha/<id>` consulta MongoDB Atlas cada vez que se abre. Muestra nombre, categoría, descripción, precio, estado y disponibilidad; no muestra contraseñas, `MONGO_URI` ni información privada de clientes.

---

# H. GitHub: guardar los avances

El proyecto usa el repositorio existente:

`https://github.com/cristianadm-wq/tienda_estilo_moda`

No inicialices otro repositorio ni reemplaces su historial. Comprueba primero:

```powershell
git status
git branch --show-current
git remote -v
git check-ignore -v .env
```

Asegúrate de que el remoto sea `tienda_estilo_moda` y que `.env` esté ignorado. Revisa `git status` antes de hacer `git add`.

Cuando las pruebas locales estén correctas, prepara y registra los archivos modificados:

```powershell
git add app.py requirements.txt .env.example README.md GUIA_PROYECTO_FINAL.md INSTRUCCIONES_RESERVA_TEMPORAL.md migrar_inventario_reservas.py validar_mongo.py templates static
git status
git commit -m "Integra reservas temporales y QR en la tienda"
git push origin NOMBRE-DE-TU-RAMA
```

Sustituye `NOMBRE-DE-TU-RAMA` por la rama donde trabajaste. La guía del profesor solicita al menos cuatro commits en el historial: conserva los commits del primer parcial y registra avances reales (respaldo, reservas, vencimiento/checkout, QR/pruebas). No crees commits vacíos solo para aparentar actividad.

---

# I. Render: actualizar el sitio existente

Actualiza el servicio existente de Tienda Estilo Moda. No crees otro servicio ni conectes el repositorio QR independiente.

Configuración habitual:

| Campo | Valor |
|---|---|
| Repositorio | `cristianadm-wq/tienda_estilo_moda` |
| Branch | La rama que contiene la versión probada |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60` |

En Environment Variables conserva/agrega:

| Key | Value |
|---|---|
| `MONGO_URI` | Tu URI de Atlas actual (no la compartas). |
| `SECRET_KEY` | La clave secreta privada ya usada por tu app. |
| `APP_BASE_URL` | `https://tienda-estilo-moda.onrender.com` |

No pongas `MONGO_URI` ni `SECRET_KEY` en GitHub o `app.py`. En Atlas → Network Access, conserva las reglas actuales y asegúrate de permitir los rangos de salida de Render. No dejes `0.0.0.0/0` como solución permanente.

Después del despliegue abre:

- `https://tienda-estilo-moda.onrender.com/`
- `https://tienda-estilo-moda.onrender.com/api/salud`
- `https://tienda-estilo-moda.onrender.com/api/catalogo`

Repite las pruebas de reserva, bloqueo, cancelación, vencimiento, compra y QR desde Render. Escanea el QR con el teléfono usando datos móviles o Wi-Fi.

---

# J. Qué entregar en Classroom

El PDF del profesor pide una entrega por equipo, nombres completos, enlaces, informe de 5 a 8 páginas y código QR probado desde celular.

Prepara:

1. **Liga de Render:** `https://tienda-estilo-moda.onrender.com`.
2. **Liga de GitHub:** `https://github.com/cristianadm-wq/tienda_estilo_moda`.
3. **Informe PDF de 5 a 8 páginas** con portada, proyecto original, reserva temporal, QR, MongoDB Atlas, pruebas, publicación y conclusión. Usa `PLANTILLA_INFORME_CLASSROOM.docx` como base editable, pega las capturas reales y exporta desde Word a PDF.
4. **Imagen QR** de un producto, incluida en el informe y escaneada desde un celular.
5. **Diez capturas** de las pruebas de la tabla anterior.
6. **Nombres completos y participación** de cada integrante.
7. **Tabla de resultados** con prueba, resultado esperado, resultado obtenido y corrección si hubo error.

No muestres contraseñas, correos privados, `MONGO_URI` ni claves secretas en las capturas.

## Portada sugerida

- CBTIS 272, Sor Juana Inés de la Cruz.
- Aplicaciones Web, 5.º semestre, grupo 5APV.
- Proyecto: Tienda Estilo Moda — Reserva temporal y código QR.
- Integrantes: Cristian Alejandro Díaz Mendoza y Jorge Pérez Pool.
- Fecha: la indicada por el docente en Classroom.

## Preguntas de cierre: respuestas guía

**¿Por qué la reserva debe guardarse en MongoDB Atlas?**  
Porque la reserva debe ser compartida entre visitantes y conservarse aunque se recargue o cierre una ventana; no puede depender solo del navegador.

**¿Qué sucede cuando dos personas reservan al mismo tiempo?**  
Flask ejecuta una actualización condicional y transaccional del inventario. La primera operación que aparta las unidades gana; la segunda no puede superar las existencias disponibles.

**¿Cuál es la diferencia entre reservado y comprado?**  
Reservado significa que las unidades están apartadas temporalmente; comprado significa que el pedido se confirmó y las existencias físicas se descontaron.

**¿Qué sucede cuando el contador llega a cero?**  
La reserva vence. Al siguiente acceso que revisa el inventario, el servidor marca `vencida` y devuelve las unidades a la disponibilidad.

---

# K. Exposición de máximo 7 minutos

| Tiempo | Qué mostrar |
|---|---|
| 1 min | Tienda original, problema y recurso reservado. |
| 3 min | A reserva, B queda bloqueado, cancelar/vencer y confirmar. |
| 1 min | Escanear QR y mostrar ficha publicada. |
| 1 min | Mostrar MongoDB Atlas, GitHub y Render. |
| 1 min | Participación, dificultad y aprendizaje. |

Todos los integrantes deben poder explicar qué se reserva, cómo se evita la doble reserva, qué ocurre al vencer, qué guarda Atlas y qué dirección contiene el QR.

---

# L. Problemas comunes

| Problema | Qué revisar |
|---|---|
| `ModuleNotFoundError: qrcode` | Ejecutar `pip install -r requirements.txt`. |
| Error de transacción | Confirmar que MongoDB sea Atlas o replica set; standalone no funciona para transacciones. |
| Error 503 en Render | Revisar `MONGO_URI`, usuario/contraseña, `SECRET_KEY`, Network Access y Logs. |
| QR abre localhost | Configurar `APP_BASE_URL` en Render con la URL HTTPS real y volver a desplegar. |
| El producto no deja reservar | Revisar `stock`, `reservadas`, talla/color, estado y drop programado. |
| La reserva no se libera | Recargar catálogo/carrito/ficha; revisar `vence_en` y estado en Atlas. |
| No se puede bajar stock en Admin | No reducir existencias por debajo de unidades reservadas. |

**Importante:** el checkout de Tienda Estilo Moda sigue siendo una simulación académica. No realiza cobros reales.
