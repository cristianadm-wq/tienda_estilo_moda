# Análisis del proyecto final y de la versión entregada

## Requisitos del PDF

El PDF pide continuar el proyecto del primer parcial, mantener tema/diseño/CRUD/datos, incorporar reserva temporal de 3 a 5 minutos, bloquear a un segundo usuario, confirmar/cancelar/vencer, persistir en MongoDB Atlas y generar un QR que abra una ficha o comprobante real de Render. También exige pruebas, GitHub, Render e informe de 5 a 8 páginas.

## Adaptación elegida

Proyecto: Tienda Estilo Moda.

Recurso reservado: cantidad de un producto, identificada por talla/color cuando corresponda.

Duración: 5 minutos.

Datos:
- Base: `tienda_estilo_moda`.
- Productos: `productos`.
- Reservas: `reservas`.
- Estados: `temporal`, `confirmada`, `cancelada`, `vencida`.
- QR: `/ficha/<id>` y `/qr/producto/<id>.png`.

## Qué incorpora el ZIP actualizado

- Reserva atómica de inventario simple o por variantes talla/color.
- Contador persistido en MongoDB por `vence_en`.
- Cancelación y vencimiento con liberación de stock y conservación del historial.
- Confirmación transaccional que descuenta inventario, crea pedido y marca reservas como confirmadas.
- Índice único parcial que impide dos reservas temporales activas de la misma sesión/producto/variante y permite conservar estados históricos.
- QR PNG público que apunta a una ficha consultada en tiempo real desde Atlas.
- `/api/salud` para comprobar el servicio y MongoDB.
- Migración del inventario y guía de instalación/pruebas/entrega.

## Comprobaciones técnicas realizadas en el paquete

- `app.py`, `migrar_inventario_reservas.py` y `probar_api.py` pasan compilación sintáctica con `py_compile`.
- Las plantillas Jinja se revisan con el parser de Jinja2.
- La librería QR está incluida en `requirements.txt`.
- El QR se genera como PNG usando la URL de `/ficha/<id>`.

## Comprobaciones que debe ejecutar el equipo

No se puede confirmar desde este entorno la conexión a tu instancia real de Atlas, el comportamiento de dos sesiones concurrentes ni el despliegue de Render porque no se incluyeron credenciales (correctamente) ni acceso a tu servicio. Ejecuta las diez pruebas de `INSTRUCCIONES_RESERVA_TEMPORAL.md` con el `.env` de tu computadora y repítelas en la URL de Render. No marques como superada una prueba hasta observar su resultado.
