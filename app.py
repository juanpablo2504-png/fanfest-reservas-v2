import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import json
import random
import string
import io
import smtplib
from email.mime.text import MIMEText
from datetime import date, datetime, timedelta

DB_PATH = "reservas.db"

# Columnas por defecto de la lista de invitados (editable desde Administración)
COLUMNAS_DEFAULT = [
    {"nombre": "Nombre completo", "requerido": True},
    {"nombre": "Teléfono o correo", "requerido": False},
]

# Campos por defecto del desglose inicial, antes de aprobar (editable desde Administración)
CAMPOS_DESGLOSE_DEFAULT = ["Empresa"]

st.set_page_config(page_title="Reservas Fan Fest", page_icon="🎫", layout="wide")

# ----------------------------------------------------------------------------
# BASE DE DATOS
# ----------------------------------------------------------------------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _add_column_if_missing(conn, table, column, coltype):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table})")
    columnas_existentes = [fila[1] for fila in c.fetchall()]
    if column not in columnas_existentes:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


def _codigo_aleatorio():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def generar_codigo_unico(conn):
    c = conn.cursor()
    while True:
        codigo = _codigo_aleatorio()
        c.execute("SELECT 1 FROM reservas WHERE codigo=?", (codigo,))
        if c.fetchone() is None:
            return codigo


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS areas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL,
        capacidad_diaria INTEGER NOT NULL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reglas (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reservas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        area TEXT NOT NULL,
        cantidad INTEGER NOT NULL,
        comentario TEXT,
        creado_en TEXT NOT NULL
    )""")
    conn.commit()

    # Migraciones: columnas nuevas para el flujo de aprobación
    for columna, tipo in [
        ("codigo", "TEXT"),
        ("estado", "TEXT"),
        ("revisado_en", "TEXT"),
        ("motivo_rechazo", "TEXT"),
        ("lista_invitados", "TEXT"),
        ("lista_subida_en", "TEXT"),
        ("solicitante_nombre", "TEXT"),
        ("solicitante_correo", "TEXT"),
        ("desglose_inicial", "TEXT"),
    ]:
        _add_column_if_missing(conn, "reservas", columna, tipo)

    # Reservas creadas antes del flujo de aprobación: se consideran ya aprobadas
    c.execute("UPDATE reservas SET estado='aprobada' WHERE estado IS NULL OR estado=''")
    c.execute("SELECT id FROM reservas WHERE codigo IS NULL OR codigo=''")
    for (rid,) in c.fetchall():
        c.execute("UPDATE reservas SET codigo=? WHERE id=?", (generar_codigo_unico(conn), rid))
    conn.commit()

    # Áreas por defecto (ya no manejan capacidad propia, es solo una etiqueta)
    c.execute("SELECT COUNT(*) FROM areas")
    if c.fetchone()[0] == 0:
        c.executemany(
            "INSERT INTO areas (nombre, capacidad_diaria) VALUES (?, 0)",
            [("General",), ("VIP",), ("Staff/Prensa",)],
        )

    # Reglas por defecto
    defaults = {
        "capacidad_diaria_total": "70",
        "max_dias_anticipacion": "4",
        "min_dias_anticipacion": "0",
        "max_boletos_por_reserva": "10",
        "admin_password": "admin123",
        "columnas_invitados": json.dumps(COLUMNAS_DEFAULT, ensure_ascii=False),
        "campos_desglose": json.dumps(CAMPOS_DESGLOSE_DEFAULT, ensure_ascii=False),
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO reglas (clave, valor) VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()


def get_rule(clave, default=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT valor FROM reglas WHERE clave=?", (clave,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_rule(clave, valor):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reglas (clave, valor) VALUES (?,?) "
        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
        (clave, str(valor)),
    )
    conn.commit()
    conn.close()


def get_columnas_invitados():
    raw = get_rule("columnas_invitados")
    if not raw:
        return [dict(col) for col in COLUMNAS_DEFAULT]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [dict(col) for col in COLUMNAS_DEFAULT]


def set_columnas_invitados(columnas):
    set_rule("columnas_invitados", json.dumps(columnas, ensure_ascii=False))


def get_campos_desglose():
    raw = get_rule("campos_desglose")
    if not raw:
        return list(CAMPOS_DESGLOSE_DEFAULT)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return list(CAMPOS_DESGLOSE_DEFAULT)


def set_campos_desglose(campos):
    set_rule("campos_desglose", json.dumps(campos, ensure_ascii=False))


def get_areas():
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM areas ORDER BY nombre", conn)
    conn.close()
    return df


def add_area(nombre):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO areas (nombre, capacidad_diaria) VALUES (?, 0)", (nombre,))
    conn.commit()
    conn.close()


def delete_area(area_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM areas WHERE id=?", (area_id,))
    conn.commit()
    conn.close()


def get_reservas(fecha_ini=None, fecha_fin=None, area=None, estado=None):
    conn = get_conn()
    query = "SELECT * FROM reservas WHERE 1=1"
    params = []
    if fecha_ini:
        query += " AND fecha >= ?"
        params.append(str(fecha_ini))
    if fecha_fin:
        query += " AND fecha <= ?"
        params.append(str(fecha_fin))
    if area:
        query += " AND area = ?"
        params.append(area)
    if estado:
        query += " AND estado = ?"
        params.append(estado)
    query += " ORDER BY fecha, area"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_reserva_by_codigo(codigo):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM reservas WHERE codigo=?", conn, params=(codigo,))
    conn.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def add_reserva(fecha, area, cantidad, comentario, solicitante_nombre, solicitante_correo, desglose_inicial):
    conn = get_conn()
    codigo = generar_codigo_unico(conn)
    c = conn.cursor()
    c.execute(
        "INSERT INTO reservas (fecha, area, cantidad, comentario, creado_en, estado, codigo, "
        "solicitante_nombre, solicitante_correo, desglose_inicial) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            str(fecha), area, cantidad, comentario, datetime.now().isoformat(), "pendiente", codigo,
            solicitante_nombre, solicitante_correo, json.dumps(desglose_inicial, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return codigo


def delete_reserva(reserva_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM reservas WHERE id=?", (reserva_id,))
    conn.commit()
    conn.close()


def reservado_aprobado_en(fecha):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COALESCE(SUM(cantidad),0) FROM reservas WHERE fecha=? AND estado='aprobada'",
        (str(fecha),),
    )
    total = c.fetchone()[0]
    conn.close()
    return total


def reservado_area_dia(fecha, area):
    """Suma lo que un área ya tiene en juego para un día (pendiente + aprobada, sin contar rechazadas)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COALESCE(SUM(cantidad),0) FROM reservas "
        "WHERE fecha=? AND area=? AND estado IN ('pendiente','aprobada')",
        (str(fecha), area),
    )
    total = c.fetchone()[0]
    conn.close()
    return total


def aprobar_reserva(reserva_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT fecha, cantidad, estado FROM reservas WHERE id=?", (reserva_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False, "No se encontró la reserva."
    fecha, cantidad, estado = row
    if estado != "pendiente":
        conn.close()
        return False, "Esta solicitud ya fue revisada."
    capacidad_total = int(get_rule("capacidad_diaria_total", 70))
    aprobado_actual = reservado_aprobado_en(fecha)
    disponible = capacidad_total - aprobado_actual
    if cantidad > disponible:
        conn.close()
        return False, f"Solo quedan {disponible} boletos disponibles para el {fecha}. No puedes aprobar {cantidad}."
    c.execute(
        "UPDATE reservas SET estado='aprobada', revisado_en=? WHERE id=?",
        (datetime.now().isoformat(), reserva_id),
    )
    conn.commit()
    conn.close()
    return True, "Solicitud aprobada."


def rechazar_reserva(reserva_id, motivo=""):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reservas SET estado='rechazada', revisado_en=?, motivo_rechazo=? WHERE id=?",
        (datetime.now().isoformat(), motivo, reserva_id),
    )
    conn.commit()
    conn.close()


def guardar_lista_invitados(reserva_id, df_invitados):
    conn = get_conn()
    c = conn.cursor()
    data_json = json.dumps(df_invitados.to_dict(orient="records"), ensure_ascii=False)
    c.execute(
        "UPDATE reservas SET lista_invitados=?, lista_subida_en=? WHERE id=?",
        (data_json, datetime.now().isoformat(), reserva_id),
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------
# EXCEL DE INVITADOS
# ----------------------------------------------------------------------------

def generar_plantilla_excel(cantidad):
    columnas = get_columnas_invitados()
    data = {"#": list(range(1, cantidad + 1))}
    for col in columnas:
        data[col["nombre"]] = ["" for _ in range(cantidad)]
    df = pd.DataFrame(data)
    return df_a_excel_bytes(df)


def df_a_excel_bytes(df):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Invitados")
    buffer.seek(0)
    return buffer.getvalue()


def validar_lista_invitados(archivo_subido, cantidad_esperada):
    try:
        df = pd.read_excel(archivo_subido, engine="openpyxl")
    except Exception as e:
        return None, f"No pude leer el archivo: {e}"

    df.columns = [str(c).strip() for c in df.columns]
    columnas = get_columnas_invitados()
    requeridas = [c["nombre"] for c in columnas if c.get("requerido")]

    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        return None, (
            f"Al archivo le falta la columna '{', '.join(faltantes)}'. "
            "Usa la plantilla descargada sin cambiarle los encabezados."
        )

    df = df.dropna(how="all").reset_index(drop=True)

    if len(df) != cantidad_esperada:
        return None, (
            f"Tu reserva es de {cantidad_esperada} boleto(s), pero el archivo tiene "
            f"{len(df)} fila(s) de invitados. Deben coincidir exactamente."
        )

    for columna in requeridas:
        vacios = df[columna].isna() | (df[columna].astype(str).str.strip() == "")
        if vacios.any():
            filas = (vacios[vacios].index + 2).tolist()  # +2: encabezado + base 1
            return None, f"Falta '{columna}' en la(s) fila(s) {filas} del Excel. Complétalo y vuelve a subirlo."

    return df, None


def verificar_admin():
    """Muestra el formulario de contraseña si no se ha iniciado sesión como admin.
    Si la contraseña es correcta, marca la sesión como admin y continúa.
    Si no, detiene la ejecución de la página actual."""
    if "is_admin" not in st.session_state:
        st.session_state.is_admin = False

    if not st.session_state.is_admin:
        st.title("🔒 Acceso de administrador")
        pwd = st.text_input("Contraseña de administrador", type="password")
        if st.button("Entrar"):
            if pwd == get_rule("admin_password"):
                st.session_state.is_admin = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        st.caption("Contraseña por defecto: admin123 (cámbiala en Administración → Seguridad).")
        st.stop()


# ----------------------------------------------------------------------------
# RESPALDO Y EXPORTES
# ----------------------------------------------------------------------------

def leer_respaldo_db():
    with open(DB_PATH, "rb") as f:
        return f.read()


def obtener_invitados_consolidados(fecha_ini, fecha_fin):
    """Junta los invitados de todas las reservas aprobadas (todas las áreas) en un rango de fechas."""
    df_aprobadas = get_reservas(fecha_ini, fecha_fin, estado="aprobada")
    filas = []
    faltantes = 0
    for _, row in df_aprobadas.iterrows():
        lista_raw = row.get("lista_invitados")
        if isinstance(lista_raw, str) and lista_raw.strip():
            invitados = json.loads(lista_raw)
            for invitado in invitados:
                fila = {"Fecha": row["fecha"], "Área": row["area"], "Código de reserva": row["codigo"]}
                fila.update(invitado)
                filas.append(fila)
        else:
            faltantes += 1
    return pd.DataFrame(filas), faltantes


# ----------------------------------------------------------------------------
# CORREO DE CONFIRMACIÓN
# ----------------------------------------------------------------------------

def enviar_correo_confirmacion(destinatario, nombre, codigo, fecha, area, cantidad):
    """Envía un correo de confirmación con el código de reserva.
    Requiere las credenciales en st.secrets['email']. Si no están configuradas,
    no rompe la app: simplemente no envía nada y avisa con un mensaje de retorno."""
    try:
        remitente = st.secrets["email"]["remitente"]
        password = st.secrets["email"]["password"]
    except Exception:
        return False, "El envío de correo no está configurado todavía (faltan credenciales en Secrets)."

    cuerpo = (
        f"Hola {nombre},\n\n"
        "Recibimos tu solicitud de boletos para el Fan Fest.\n\n"
        f"Código de reserva: {codigo}\n"
        f"Fecha: {fecha}\n"
        f"Área: {area}\n"
        f"Cantidad de boletos: {cantidad}\n\n"
        "Guarda este código: lo vas a necesitar para consultar el estado de tu solicitud "
        "y, si se aprueba, subir la lista detallada de tus invitados.\n\n"
        "Este es un correo automático, por favor no respondas a este mensaje."
    )
    mensaje = MIMEText(cuerpo, "plain", "utf-8")
    mensaje["Subject"] = f"Confirmación de tu solicitud - código {codigo}"
    mensaje["From"] = remitente
    mensaje["To"] = destinatario

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(remitente, password)
            servidor.sendmail(remitente, destinatario, mensaje.as_string())
        return True, "Correo enviado."
    except Exception as e:
        return False, f"No se pudo enviar el correo: {e}"


init_db()

# ----------------------------------------------------------------------------
# NAVEGACIÓN
# ----------------------------------------------------------------------------

st.sidebar.title("🎫 Fan Fest")
pagina = st.sidebar.radio("Ir a:", ["Solicitar reserva", "Mi reserva", "Dashboard 🔒", "Administración"])

# ----------------------------------------------------------------------------
# PÁGINA: SOLICITAR RESERVA
# ----------------------------------------------------------------------------

if pagina == "Solicitar reserva":
    st.title("🎫 Solicitar boletos")

    areas_df = get_areas()
    if areas_df.empty:
        st.warning("Todavía no hay áreas configuradas. Ve a Administración para crear una.")
        st.stop()

    capacidad_total = int(get_rule("capacidad_diaria_total", 70))
    max_dias = int(get_rule("max_dias_anticipacion", 4))
    min_dias = int(get_rule("min_dias_anticipacion", 0))
    max_por_reserva = int(get_rule("max_boletos_por_reserva", 10))

    hoy = date.today()
    fecha_min = hoy + timedelta(days=min_dias)
    fecha_max = hoy + timedelta(days=max_dias)

    st.caption(
        f"📌 Cupo de {capacidad_total} boletos por día · puedes solicitar entre "
        f"{fecha_min.strftime('%d/%b')} y {fecha_max.strftime('%d/%b')} · "
        f"máximo {max_por_reserva} boletos por área, por día (entre todas tus solicitudes)."
    )
    st.info(
        "Tu solicitud queda **pendiente de aprobación**. Te daremos un código de reserva: "
        "guárdalo para consultar el resultado en la pestaña **Mi reserva**, y si se aprueba, "
        "subir ahí la lista de tus invitados."
    )

    st.markdown("**Tus datos**")
    col_nombre, col_correo = st.columns(2)
    with col_nombre:
        solicitante_nombre = st.text_input("Tu nombre completo")
    with col_correo:
        solicitante_correo = st.text_input("Tu correo electrónico")

    st.markdown("**Tu solicitud**")
    col1, col2 = st.columns(2)
    with col1:
        area_sel = st.selectbox("Área", areas_df["nombre"].tolist())
    with col2:
        fecha_sel = st.date_input("Fecha", value=fecha_min, min_value=fecha_min, max_value=fecha_max)

    aprobado_dia = reservado_aprobado_en(fecha_sel)
    disponible_dia = max(0, capacidad_total - aprobado_dia)
    st.info(
        f"Boletos ya **aprobados** para el **{fecha_sel.strftime('%d/%m/%Y')}**: "
        f"**{aprobado_dia}** de {capacidad_total} · quedan **{disponible_dia}** disponibles "
        "(las solicitudes pendientes de otras personas todavía no se han restado)."
    )

    ya_area_dia = reservado_area_dia(fecha_sel, area_sel)
    restante_area_dia = max(0, max_por_reserva - ya_area_dia)

    st.info(
        f"El área **{area_sel}** ya tiene **{ya_area_dia}** de **{max_por_reserva}** boletos "
        f"solicitados/aprobados para el {fecha_sel.strftime('%d/%m/%Y')} "
        "(sumando solicitudes pendientes y aprobadas)."
    )

    if restante_area_dia == 0:
        st.error(
            f"El área **{area_sel}** ya alcanzó el máximo de boletos permitido para este día. "
            "No se pueden enviar más solicitudes para esta combinación de área y fecha."
        )
    else:
        if restante_area_dia < max_por_reserva:
            st.caption(f"Como ya tiene {ya_area_dia} en juego, esta solicitud puede ser de hasta {restante_area_dia} boletos.")

        cantidad = st.number_input("Cantidad de boletos", min_value=1, max_value=restante_area_dia, value=1, step=1)
        if cantidad > disponible_dia:
            st.warning(
                f"Ojo: estás pidiendo más boletos ({cantidad}) que los disponibles según lo ya aprobado "
                f"({disponible_dia}). Puedes enviar tu solicitud, pero podría rechazarse por falta de cupo."
            )

        # --- Desglose: ¿para quién son los boletos? ---
        contexto_actual = f"{area_sel}|{fecha_sel}|{cantidad}"
        if st.session_state.get("desglose_contexto") != contexto_actual:
            st.session_state.desglose_contexto = contexto_actual
            st.session_state.desglose_grupos = []

        asignado = sum(g["Boletos"] for g in st.session_state.desglose_grupos)
        faltan = cantidad - asignado

        st.divider()
        st.markdown("**¿Para quién son estos boletos?**")
        st.caption(f"De los {cantidad} boletos, llevas asignados **{asignado}**. Faltan **{faltan}**.")

        if st.session_state.desglose_grupos:
            st.dataframe(pd.DataFrame(st.session_state.desglose_grupos), hide_index=True, use_container_width=True)
            if st.button("↩️ Quitar el último grupo agregado"):
                st.session_state.desglose_grupos.pop()
                st.rerun()

        campos_desglose = get_campos_desglose()

        if faltan > 0:
            with st.form("form_grupo_desglose", clear_on_submit=True):
                valores_campos = {}
                for campo in campos_desglose:
                    valores_campos[campo] = st.text_input(campo)
                boletos_grupo = st.number_input(
                    "Boletos para este grupo", min_value=1, max_value=faltan, value=min(faltan, 1)
                )
                agregar = st.form_submit_button("Agregar grupo")
                if agregar:
                    if any(not v.strip() for v in valores_campos.values()):
                        st.error("Completa todos los campos del grupo.")
                    else:
                        nuevo_grupo = {campo: valor.strip() for campo, valor in valores_campos.items()}
                        nuevo_grupo["Boletos"] = int(boletos_grupo)
                        st.session_state.desglose_grupos.append(nuevo_grupo)
                        st.rerun()
        else:
            st.success("✅ Ya asignaste los boletos completos. Puedes continuar.")

            comentario = st.text_input("Nota (opcional)")

            if st.button("Enviar solicitud", type="primary"):
                nombre_limpio = solicitante_nombre.strip()
                correo_limpio = solicitante_correo.strip()
                if not nombre_limpio:
                    st.error("Escribe tu nombre completo.")
                elif "@" not in correo_limpio or "." not in correo_limpio:
                    st.error("Escribe un correo electrónico válido.")
                else:
                    codigo = add_reserva(
                        fecha_sel, area_sel, cantidad, comentario, nombre_limpio, correo_limpio,
                        st.session_state.desglose_grupos,
                    )
                    enviado, _ = enviar_correo_confirmacion(
                        correo_limpio, nombre_limpio, codigo, fecha_sel, area_sel, cantidad
                    )
                    st.success(
                        f"¡Solicitud enviada! Tu código de reserva es **{codigo}**. Guárdalo: lo vas a necesitar "
                        "en 'Mi reserva' para ver si fue aprobada y subir tu lista de invitados."
                    )
                    if enviado:
                        st.caption(f"📧 Te enviamos un correo de confirmación a {correo_limpio}.")
                    else:
                        st.caption("No pudimos enviarte un correo de confirmación, pero tu código sigue siendo válido — guárdalo de todos modos.")
                    st.session_state.desglose_grupos = []
                    st.session_state.desglose_contexto = None

# ----------------------------------------------------------------------------
# PÁGINA: MI RESERVA
# ----------------------------------------------------------------------------

elif pagina == "Mi reserva":
    st.title("🔎 Mi reserva")

    codigo_input = st.text_input("Ingresa tu código de reserva", key="codigo_input").strip().upper()
    if st.button("Buscar"):
        st.session_state.codigo_consulta = codigo_input

    codigo_actual = st.session_state.get("codigo_consulta", "")

    if codigo_actual:
        reserva = get_reserva_by_codigo(codigo_actual)
        if reserva is None:
            st.error("No encontramos ninguna reserva con ese código. Revisa que esté bien escrito.")
        else:
            st.subheader(f"Reserva {reserva['codigo']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Fecha", reserva["fecha"])
            c2.metric("Área", reserva["area"])
            c3.metric("Boletos", reserva["cantidad"])
            st.caption(f"Solicitado por: {reserva.get('solicitante_nombre') or '—'} · {reserva.get('solicitante_correo') or '—'}")

            estado = reserva["estado"]
            if estado == "pendiente":
                st.warning("⏳ Tu solicitud está **pendiente de revisión**. Vuelve a consultar más tarde.")

            elif estado == "rechazada":
                st.error("❌ Tu solicitud fue **rechazada**.")
                if reserva.get("motivo_rechazo"):
                    st.caption(f"Motivo: {reserva['motivo_rechazo']}")

            elif estado == "aprobada":
                st.success("✅ ¡Tu solicitud fue **aprobada**!")

                lista_raw = reserva.get("lista_invitados")
                ya_tiene_lista = isinstance(lista_raw, str) and lista_raw.strip()

                if ya_tiene_lista:
                    st.info("Ya recibimos tu lista de invitados. Si necesitas corregirla, puedes volver a subir el archivo.")
                    invitados_df = pd.DataFrame(json.loads(lista_raw))
                    st.dataframe(invitados_df, hide_index=True, use_container_width=True)
                else:
                    st.markdown("**Siguiente paso: sube la lista de tus invitados**")

                st.write("1️⃣ Descarga la plantilla y llénala (no cambies los encabezados):")
                plantilla = generar_plantilla_excel(int(reserva["cantidad"]))
                st.download_button(
                    "⬇️ Descargar plantilla de invitados",
                    plantilla,
                    file_name=f"invitados_{reserva['codigo']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

                st.write("2️⃣ Sube el archivo ya lleno:")
                archivo = st.file_uploader(
                    "Archivo de invitados (.xlsx)", type=["xlsx"], key=f"upload_{reserva['codigo']}"
                )
                if archivo is not None:
                    df_validado, error = validar_lista_invitados(archivo, int(reserva["cantidad"]))
                    if error:
                        st.error(error)
                    else:
                        columnas_guardar = [c["nombre"] for c in get_columnas_invitados() if c["nombre"] in df_validado.columns]
                        guardar_lista_invitados(int(reserva["id"]), df_validado[columnas_guardar])
                        st.success("¡Lista de invitados recibida! Ya está todo listo. 🎉")
                        st.rerun()

# ----------------------------------------------------------------------------
# PÁGINA: DASHBOARD
# ----------------------------------------------------------------------------

elif pagina == "Dashboard 🔒":
    verificar_admin()
    st.title("📊 Dashboard de reservas")

    hoy = date.today()
    col1, col2 = st.columns(2)
    with col1:
        fecha_ini = st.date_input("Desde", value=hoy)
    with col2:
        fecha_fin = st.date_input("Hasta", value=hoy + timedelta(days=7))

    if fecha_ini > fecha_fin:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        st.stop()

    capacidad_diaria_total = int(get_rule("capacidad_diaria_total", 70))
    num_dias = (fecha_fin - fecha_ini).days + 1
    capacidad_total_rango = capacidad_diaria_total * num_dias

    df_todas = get_reservas(fecha_ini, fecha_fin)
    df_aprobadas = df_todas[df_todas["estado"] == "aprobada"] if not df_todas.empty else df_todas
    df_pendientes = df_todas[df_todas["estado"] == "pendiente"] if not df_todas.empty else df_todas

    total_aprobado = int(df_aprobadas["cantidad"].sum()) if not df_aprobadas.empty else 0
    ocupacion = (total_aprobado / capacidad_total_rango * 100) if capacidad_total_rango else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Boletos aprobados", total_aprobado)
    m2.metric("Capacidad del rango", capacidad_total_rango)
    m3.metric("Ocupación", f"{ocupacion:.1f}%")
    m4.metric("Solicitudes pendientes", len(df_pendientes))

    if df_aprobadas.empty:
        st.info("No hay reservas aprobadas en este rango de fechas todavía.")
    else:
        st.subheader("Boletos aprobados por día y área")
        fig = px.bar(
            df_aprobadas, x="fecha", y="cantidad", color="area", barmode="stack",
            labels={"fecha": "Fecha", "cantidad": "Boletos", "area": "Área"},
        )
        st.plotly_chart(fig, use_container_width=True)

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Total por área")
            por_area = df_aprobadas.groupby("area")["cantidad"].sum().reset_index()
            fig2 = px.pie(por_area, names="area", values="cantidad")
            st.plotly_chart(fig2, use_container_width=True)
        with c2:
            st.subheader("Total por día")
            por_dia = df_aprobadas.groupby("fecha")["cantidad"].sum().reset_index()
            fig3 = px.bar(por_dia, x="fecha", y="cantidad")
            st.plotly_chart(fig3, use_container_width=True)

        st.subheader("Detalle de reservas aprobadas")
        st.dataframe(df_aprobadas, hide_index=True, use_container_width=True)
        st.download_button(
            "⬇️ Descargar CSV",
            df_aprobadas.to_csv(index=False).encode("utf-8"),
            file_name=f"reservas_{fecha_ini}_{fecha_fin}.csv",
            mime="text/csv",
        )

    if not df_pendientes.empty:
        st.warning(
            f"Hay {len(df_pendientes)} solicitud(es) pendiente(s) en este rango — "
            "revísalas en Administración → Solicitudes pendientes."
        )

# ----------------------------------------------------------------------------
# PÁGINA: ADMINISTRACIÓN
# ----------------------------------------------------------------------------

elif pagina == "Administración":
    verificar_admin()
    st.title("⚙️ Administración")

    tab_solicitudes, tab_reglas, tab_areas, tab_plantilla, tab_reservas, tab_respaldo, tab_seguridad = st.tabs(
        ["Solicitudes pendientes", "Reglas", "Áreas", "Formularios", "Reservas", "Respaldo y exportar", "Seguridad"]
    )

    # --- Solicitudes pendientes ---
    with tab_solicitudes:
        st.subheader("Solicitudes pendientes de revisión")
        df_pend = get_reservas(estado="pendiente")
        if df_pend.empty:
            st.info("No hay solicitudes pendientes. 🎉")
        else:
            capacidad_total = int(get_rule("capacidad_diaria_total", 70))
            for _, row in df_pend.iterrows():
                aprobado_dia = reservado_aprobado_en(row["fecha"])
                disponible_dia = max(0, capacidad_total - aprobado_dia)
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
                    c1.write(f"**Código:** {row['codigo']}")
                    c2.write(f"**Fecha:** {row['fecha']}")
                    c3.write(f"**Área:** {row['area']}")
                    c4.write(f"**Cantidad:** {row['cantidad']} · Disponibles ese día: {disponible_dia}")
                    st.caption(f"Solicitante: {row.get('solicitante_nombre') or '—'} · {row.get('solicitante_correo') or '—'}")
                    if row["comentario"]:
                        st.caption(f"Nota: {row['comentario']}")

                    desglose_raw = row.get("desglose_inicial")
                    if isinstance(desglose_raw, str) and desglose_raw.strip():
                        try:
                            desglose = json.loads(desglose_raw)
                        except json.JSONDecodeError:
                            desglose = []
                        if desglose:
                            st.write("Desglose declarado por el solicitante:")
                            st.dataframe(pd.DataFrame(desglose), hide_index=True, use_container_width=True)

                    colA, colB = st.columns(2)
                    with colA:
                        if st.button("✅ Aprobar", key=f"aprobar_{row['id']}"):
                            ok, msg = aprobar_reserva(int(row["id"]))
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)
                            st.rerun()
                    with colB:
                        motivo = st.text_input("Motivo de rechazo (opcional)", key=f"motivo_{row['id']}")
                        if st.button("❌ Rechazar", key=f"rechazar_{row['id']}"):
                            rechazar_reserva(int(row["id"]), motivo)
                            st.warning("Solicitud rechazada.")
                            st.rerun()

    # --- Reglas ---
    with tab_reglas:
        st.subheader("Reglas de reservación")
        capacidad_total_input = st.number_input(
            "Cupo total de boletos por día",
            min_value=1, value=int(get_rule("capacidad_diaria_total", 70)),
        )
        max_dias = st.number_input(
            "Máximo de días de anticipación para solicitar",
            min_value=0, value=int(get_rule("max_dias_anticipacion", 4)),
        )
        min_dias = st.number_input(
            "Mínimo de días de anticipación (0 = se puede el mismo día)",
            min_value=0, value=int(get_rule("min_dias_anticipacion", 0)),
        )
        max_por_reserva = st.number_input(
            "Máximo de boletos por área, por día (acumulado entre todas sus solicitudes)",
            min_value=1, value=int(get_rule("max_boletos_por_reserva", 10)),
        )
        if st.button("Guardar reglas", type="primary"):
            set_rule("capacidad_diaria_total", capacidad_total_input)
            set_rule("max_dias_anticipacion", max_dias)
            set_rule("min_dias_anticipacion", min_dias)
            set_rule("max_boletos_por_reserva", max_por_reserva)
            st.success("Reglas actualizadas.")

    # --- Áreas ---
    with tab_areas:
        st.subheader("Áreas")
        st.caption("Las áreas ya no tienen capacidad propia: solo sirven para clasificar las reservas. El cupo es el total diario definido en Reglas.")
        areas_df = get_areas()
        st.dataframe(areas_df[["nombre"]], hide_index=True, use_container_width=True)

        st.markdown("**Agregar nueva área**")
        c1, c2 = st.columns([3, 1])
        with c1:
            nueva_area_nombre = st.text_input("Nombre del área", key="new_area_name")
        with c2:
            st.write("")
            if st.button("Agregar área"):
                if nueva_area_nombre.strip():
                    try:
                        add_area(nueva_area_nombre.strip())
                        st.success("Área agregada.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un área con ese nombre.")
                else:
                    st.error("Escribe un nombre para el área.")

        st.markdown("**Eliminar área**")
        if not areas_df.empty:
            c1, c2 = st.columns([3, 1])
            with c1:
                area_del = st.selectbox("Área a eliminar", areas_df["nombre"].tolist(), key="del_area_sel")
            with c2:
                st.write("")
                if st.button("Eliminar", type="secondary"):
                    area_id = int(areas_df.loc[areas_df["nombre"] == area_del, "id"].iloc[0])
                    delete_area(area_id)
                    st.success("Área eliminada.")
                    st.rerun()

    # --- Plantilla de invitados ---
    with tab_plantilla:
        st.subheader("Desglose inicial (al solicitar, antes de aprobar)")
        st.caption(
            "Estos son los campos que la persona debe llenar grupo por grupo al solicitar boletos "
            "(ej. 'Empresa'), antes de mandar la solicitud. El campo 'Boletos' siempre se incluye automáticamente."
        )

        campos_desglose = get_campos_desglose()
        if campos_desglose:
            st.dataframe(pd.DataFrame({"Campo": campos_desglose}), hide_index=True, use_container_width=True)
        else:
            st.warning("No hay campos configurados todavía.")

        st.markdown("**Agregar campo**")
        c1, c2 = st.columns([3, 1])
        with c1:
            nuevo_campo_nombre = st.text_input("Nombre del campo", key="nuevo_campo_desglose")
        with c2:
            st.write("")
            if st.button("Agregar campo"):
                nombre_limpio = nuevo_campo_nombre.strip()
                if not nombre_limpio:
                    st.error("Escribe un nombre para el campo.")
                elif nombre_limpio in campos_desglose:
                    st.error("Ya existe un campo con ese nombre.")
                else:
                    campos_desglose.append(nombre_limpio)
                    set_campos_desglose(campos_desglose)
                    st.success("Campo agregado.")
                    st.rerun()

        if campos_desglose:
            st.markdown("**Quitar campo**")
            c1, c2 = st.columns([3, 1])
            with c1:
                campo_quitar = st.selectbox("Campo a quitar", campos_desglose, key="campo_quitar_desglose")
            with c2:
                st.write("")
                if st.button("Quitar campo", type="secondary"):
                    campos_restantes = [c for c in campos_desglose if c != campo_quitar]
                    if not campos_restantes:
                        st.error("Debe quedar al menos un campo.")
                    else:
                        set_campos_desglose(campos_restantes)
                        st.success("Campo eliminado.")
                        st.rerun()

        st.divider()
        st.subheader("Columnas de la lista de invitados")
        st.caption(
            "Estas son las columnas que la gente debe llenar al subir su lista de invitados "
            "después de ser aprobados. La columna '#' siempre se agrega automáticamente para numerar."
        )

        columnas = get_columnas_invitados()
        if columnas:
            tabla = pd.DataFrame(columnas).rename(columns={"nombre": "Columna", "requerido": "Obligatoria"})
            st.dataframe(tabla, hide_index=True, use_container_width=True)
        else:
            st.warning("No hay columnas configuradas todavía.")

        st.markdown("**Agregar columna**")
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            nueva_col_nombre = st.text_input("Nombre de la columna", key="nueva_col_nombre")
        with c2:
            nueva_col_requerida = st.checkbox("Obligatoria", value=False, key="nueva_col_requerida")
        with c3:
            st.write("")
            if st.button("Agregar columna"):
                nombre_limpio = nueva_col_nombre.strip()
                if not nombre_limpio:
                    st.error("Escribe un nombre para la columna.")
                elif nombre_limpio in [c["nombre"] for c in columnas]:
                    st.error("Ya existe una columna con ese nombre.")
                else:
                    columnas.append({"nombre": nombre_limpio, "requerido": nueva_col_requerida})
                    set_columnas_invitados(columnas)
                    st.success("Columna agregada.")
                    st.rerun()

        if columnas:
            st.markdown("**Marcar / desmarcar como obligatoria**")
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                col_editar = st.selectbox("Columna", [c["nombre"] for c in columnas], key="col_editar_sel")
            with c2:
                req_actual = next((c["requerido"] for c in columnas if c["nombre"] == col_editar), False)
                nueva_req = st.checkbox("Obligatoria", value=req_actual, key="col_editar_req")
            with c3:
                st.write("")
                if st.button("Actualizar"):
                    for c in columnas:
                        if c["nombre"] == col_editar:
                            c["requerido"] = nueva_req
                    set_columnas_invitados(columnas)
                    st.success("Columna actualizada.")
                    st.rerun()

            st.markdown("**Quitar columna**")
            c1, c2 = st.columns([3, 1])
            with c1:
                col_quitar = st.selectbox("Columna a quitar", [c["nombre"] for c in columnas], key="col_quitar_sel")
            with c2:
                st.write("")
                if st.button("Quitar columna", type="secondary"):
                    columnas_restantes = [c for c in columnas if c["nombre"] != col_quitar]
                    if not columnas_restantes:
                        st.error("Debe quedar al menos una columna.")
                    else:
                        set_columnas_invitados(columnas_restantes)
                        st.success("Columna eliminada.")
                        st.rerun()

    # --- Reservas ---
    with tab_reservas:
        st.subheader("Todas las reservas")
        areas_df = get_areas()
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            f_ini = st.date_input("Desde", value=date.today(), key="admin_f_ini")
        with c2:
            f_fin = st.date_input("Hasta", value=date.today() + timedelta(days=30), key="admin_f_fin")
        with c3:
            area_filtro = st.selectbox("Área (opcional)", ["Todas"] + areas_df["nombre"].tolist())
        with c4:
            estado_filtro = st.selectbox("Estado", ["Todos", "pendiente", "aprobada", "rechazada"])

        df = get_reservas(
            f_ini, f_fin,
            None if area_filtro == "Todas" else area_filtro,
            None if estado_filtro == "Todos" else estado_filtro,
        )
        columnas_mostrar = ["id", "codigo", "fecha", "area", "cantidad", "estado", "solicitante_nombre", "solicitante_correo", "comentario"]
        st.dataframe(df[columnas_mostrar] if not df.empty else df, hide_index=True, use_container_width=True)

        if not df.empty:
            st.markdown("**Ver detalle / cancelar una reserva**")
            id_sel = st.selectbox("ID de la reserva", df["id"].tolist())
            fila = df[df["id"] == id_sel].iloc[0]

            st.write(f"**Código:** {fila['codigo']} · **Estado:** {fila['estado']}")
            st.caption(f"Solicitante: {fila.get('solicitante_nombre') or '—'} · {fila.get('solicitante_correo') or '—'}")
            if fila["estado"] == "rechazada" and fila.get("motivo_rechazo"):
                st.caption(f"Motivo de rechazo: {fila['motivo_rechazo']}")

            desglose_raw = fila.get("desglose_inicial")
            if isinstance(desglose_raw, str) and desglose_raw.strip():
                try:
                    desglose = json.loads(desglose_raw)
                except json.JSONDecodeError:
                    desglose = []
                if desglose:
                    st.write("Desglose declarado por el solicitante:")
                    st.dataframe(pd.DataFrame(desglose), hide_index=True, use_container_width=True)

            lista_raw = fila.get("lista_invitados")
            if isinstance(lista_raw, str) and lista_raw.strip():
                invitados_df = pd.DataFrame(json.loads(lista_raw))
                st.write("Lista de invitados:")
                st.dataframe(invitados_df, hide_index=True, use_container_width=True)
                st.download_button(
                    "⬇️ Descargar lista de invitados",
                    df_a_excel_bytes(invitados_df),
                    file_name=f"invitados_{fila['codigo']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            else:
                st.caption("Esta reserva todavía no tiene lista de invitados subida.")

            if st.button("🗑️ Cancelar / eliminar esta reserva", type="secondary"):
                delete_reserva(int(id_sel))
                st.success("Reserva eliminada.")
                st.rerun()

    # --- Respaldo y exportar ---
    with tab_respaldo:
        st.subheader("Respaldo completo de la base de datos")
        st.caption(
            "Descarga el archivo completo (reservas, áreas, reglas y listas de invitados). "
            "Si algo le pasa a la app, puedes restaurar todo reemplazando el archivo reservas.db con este."
        )
        try:
            datos_respaldo = leer_respaldo_db()
            st.download_button(
                "⬇️ Descargar respaldo completo (.db)",
                datos_respaldo,
                file_name=f"respaldo_reservas_{date.today().isoformat()}.db",
                mime="application/octet-stream",
            )
        except FileNotFoundError:
            st.warning("Todavía no se ha creado la base de datos (no hay nada que respaldar).")

        st.divider()

        st.subheader("Excel consolidado de invitados (todas las áreas)")
        st.caption("Junta en un solo Excel a todos los invitados de las reservas aprobadas dentro del rango de fechas que elijas.")

        c1, c2 = st.columns(2)
        with c1:
            f_export_ini = st.date_input("Desde", value=date.today(), key="export_f_ini")
        with c2:
            f_export_fin = st.date_input("Hasta", value=date.today() + timedelta(days=7), key="export_f_fin")

        if f_export_ini > f_export_fin:
            st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        else:
            df_invitados_consolidado, faltantes = obtener_invitados_consolidados(f_export_ini, f_export_fin)

            if faltantes:
                st.warning(
                    f"{faltantes} reserva(s) aprobada(s) en este rango todavía no han subido su lista de invitados "
                    "— no están incluidas en este Excel."
                )

            if df_invitados_consolidado.empty:
                st.info("No hay invitados registrados todavía en este rango de fechas.")
            else:
                st.dataframe(df_invitados_consolidado, hide_index=True, use_container_width=True)
                st.download_button(
                    "⬇️ Descargar Excel de invitados (todas las áreas)",
                    df_a_excel_bytes(df_invitados_consolidado),
                    file_name=f"invitados_{f_export_ini}_{f_export_fin}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

    # --- Seguridad ---
    with tab_seguridad:
        st.subheader("Cambiar contraseña de administrador")
        nueva_pwd = st.text_input("Nueva contraseña", type="password")
        if st.button("Actualizar contraseña"):
            if nueva_pwd.strip():
                set_rule("admin_password", nueva_pwd.strip())
                st.success("Contraseña actualizada.")
            else:
                st.error("La contraseña no puede estar vacía.")

        st.divider()
        if st.button("Cerrar sesión de administrador"):
            st.session_state.is_admin = False
            st.rerun()
