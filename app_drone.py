import streamlit as st
import numpy as np
import plotly.express as px
import random
import requests
import io

# --- IDS EXTRAÍDOS DE TUS LINKS ---
FILE_IDS = [
    "1gJ5NQa28zOp6MWd5zpUM9qCgMD14U_cj",
    "1MVBM2BCxQpusyz-TDGkoDI84L08U19zt",
    "1Y6otvOxmkla8y6xquYu8QNpbU3YpAZSY"
]

st.set_page_config(page_title="Drone Detector - USTA 2026", layout="wide")

# --- 1. SISTEMA DE LOGIN (Requerido por Rúbrica) ---
def login():
    if 'autenticado' not in st.session_state:
        st.session_state.autenticado = False

    if not st.session_state.autenticado:
        st.title("🛡️ Acceso al Sistema de Vigilancia")
        with st.form("login_form"):
            usuario = st.selectbox("Seleccione su Rol:", ["Superadministrador", "Operador"])
            password = st.text_input("Contraseña:", type="password")
            submit = st.form_submit_button("Ingresar")
            
            if submit:
                # Credenciales para el evaluador y la policía
                if (usuario == "Superadministrador" and password == "admin2026") or \
                   (usuario == "Operador" and password == "policia2026"):
                    st.session_state.autenticado = True
                    st.session_state.rol = usuario
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas")
        st.stop()

# --- 2. CARGA ON-DEMAND (STREAMING) ---
def cargar_espectrograma_streaming(file_id):
    try:
        url = f'https://drive.google.com/uc?id={file_id}'
        response = requests.get(url)
        response.raise_for_status()
        
        with io.BytesIO(response.content) as f:
            data_npz = np.load(f)
            clave = data_npz.files[0]
            matriz = data_npz[clave]
            
            if len(matriz.shape) > 2:
                matriz = matriz[0]
            return matriz
    except Exception as e:
        st.error(f"Error de conexión con Drive: {e}")
        return np.ones((40, 120))

# Ejecutar Autenticación
login()

# --- 3. INTERFAZ PRINCIPAL ---
st.sidebar.write(f"👤 Rol: **{st.session_state.rol}**")
if st.sidebar.button("Cerrar Sesión"):
    st.session_state.autenticado = False
    st.rerun()

st.title("🛡️ Monitor de Espectro - Drone Detection")

if 'alerta' not in st.session_state:
    st.session_state.alerta = False

col1, col2 = st.columns([3, 1])

with col1:
    if not st.session_state.alerta:
        # Estado de espera: Pantalla plana
        datos = np.ones((40, 120))
        fig = px.imshow(datos, color_continuous_scale='gray', zmin=0, zmax=1, aspect="auto")
        fig.update_layout(coloraxis_showscale=False)
    else:
        # Estado de detección: Elige uno de los 3 IDs al azar cada vez
        with st.spinner('Obteniendo señal real de Drive...'):
            id_sel = random.choice(FILE_IDS)
            datos = cargar_espectrograma_streaming(id_sel)
            
        fig = px.imshow(datos, color_continuous_scale='Inferno', aspect="auto")
    
    fig.update_layout(height=600, margin=dict(l=0, r=0, b=0, t=0))
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("🚨 Control de Alerta")
    if st.session_state.alerta:
        st.error("🚨 DRON DETECTADO")
    else:
        st.success("🛰️ ESCANEANDO...")

    if st.button("Simular Detección", use_container_width=True):
        st.session_state.alerta = True
        st.rerun()
        
    if st.button("Resetear", use_container_width=True):
        st.session_state.alerta = False
        st.rerun()

    st.write("---")
    st.caption("Carga de señales IQ on-demand vía streaming.")
