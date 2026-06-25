# Reservas Fan Fest 🎫

App en Streamlit para solicitar boletos diarios con flujo de aprobación,
cupo global ajustable y recolección de listas de invitados.

## Cómo correrla

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se abrirá en `http://localhost:8501`.

## Flujo

1. **Solicitar reserva** (público): cualquier persona elige área, fecha y
   cantidad. La solicitud queda **pendiente** y recibe un **código de
   reserva** (ej. `PAZ44D`) — debe guardarlo.
2. **Administración → Solicitudes pendientes** (contraseña): el equipo
   organizador aprueba o rechaza cada solicitud. Solo al **aprobar** se
   resta del cupo diario; si ya no hay cupo suficiente, el sistema no deja
   aprobar.
3. **Mi reserva** (público, con el código): la persona consulta el estado.
   Si fue aprobada, descarga una plantilla de Excel con tantas filas como
   boletos aprobados, la llena con los nombres de sus invitados, y la sube
   de vuelta — el sistema valida que el número de filas y los nombres estén
   completos antes de aceptarla.
4. **Dashboard**: ocupación y reservas aprobadas por día/área, más un
   contador de solicitudes pendientes.

## Reglas ajustables (Administración → Reglas)

- Cupo total de boletos por día (por defecto **70**, ya no es por área)
- Máximo y mínimo de días de anticipación para solicitar
- Máximo de boletos por solicitud

## Áreas (Administración → Áreas)

Ahora son solo una **etiqueta** para clasificar reservas (ya no tienen
capacidad propia) — el límite real es el cupo diario total.

## Plantilla de invitados

Columnas: `#`, `Nombre completo`, `Teléfono o correo`. Si quieres agregar
más campos (ej. identificación oficial), se ajusta en la función
`generar_plantilla_excel` y en `validar_lista_invitados` de `app.py`.

## Contraseña de administrador

Por defecto: `admin123` — cámbiala en Administración → Seguridad apenas
despliegues la app.

## Datos

Todo vive en `reservas.db` (SQLite), creado automáticamente la primera vez
que corres la app. Si ya tenías una versión anterior desplegada, al subir
este `app.py` la base se **migra sola** (las reservas viejas quedan como
"aprobadas" y reciben un código automático) — no se pierde nada.

## Nota sobre despliegue

Si la subes a Streamlit Community Cloud, recuerda que `reservas.db` puede
reiniciarse si la app se redespliega o duerme por inactividad. Para uso con
muchos usuarios simultáneos a largo plazo, conviene migrar a una base
externa (Postgres/Supabase) — el código está organizado para que ese
cambio solo toque las funciones de la sección "BASE DE DATOS".
