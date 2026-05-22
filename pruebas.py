"""
servidor.py — Backend WebSocket del detector de drones

Arranca el WebSocket en ws://localhost:8765
La lógica de detección NO fue modificada, solo se envuelve en async
para transmitir el estado a la página web en tiempo real.

Uso:
    python servidor.py
"""

import numpy as np
import subprocess
import time
import asyncio
import json
import websockets
from websockets.server import serve

# ══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE RF  (sin cambios)
# ══════════════════════════════════════════════════════════════
SAMPLE_RATE        = 10_000_000
FRECUENCIA_CENTRAL = 2_399_045_000
TAMANO_BLOQUE      = 131072
FFT_SIZE           = 1024

# ══════════════════════════════════════════════════════════════
#  PARÁMETROS DEL DETECTOR  (sin cambios)
# ══════════════════════════════════════════════════════════════
MULTIPLICADOR_UMBRAL      = 3.5
BINS_ACTIVOS_MIN          = 3
PUNTOS_POR_IMPACTO        = 2.0
DECAY_RATE                = 3.0
UMBRAL_SCORE_ACTIVACION   = 3.0
UMBRAL_SCORE_LIBERACION   = 0.8
SCORE_MAXIMO              = 10.0

# ══════════════════════════════════════════════════════════════
#  ESTADO GLOBAL COMPARTIDO  (lo lee el WebSocket)
# ══════════════════════════════════════════════════════════════
estado = {
    "score": 0.0,
    "alerta_activa": False,
    "bins_activos": 0,
    "piso_ruido": 0.0,
    "fft_mag": [],          # espectro completo para la gráfica
    "n_bloques": 0,
    "umbral_local": 0.0,
}

clientes_conectados: set = set()


# ══════════════════════════════════════════════════════════════
#  LÓGICA DE DETECCIÓN  (idéntica al original, ahora async)
# ══════════════════════════════════════════════════════════════

def procesar_espectro(raw: bytes):
    """Sin cambios respecto al original."""
    if not raw:
        return None
    datos = np.frombuffer(raw, dtype=np.int8)
    datos = datos[:len(datos) & ~1]
    if len(datos) < FFT_SIZE * 2:
        return None

    I = datos[0::2].astype(np.float32) / 128.0
    Q = datos[1::2].astype(np.float32) / 128.0
    iq = I + 1j * Q

    segmento   = iq[:4096]
    matrix_iq  = segmento.reshape(-1, FFT_SIZE)
    fft_matrix = np.abs(np.fft.fftshift(np.fft.fft(matrix_iq, axis=1), axes=1))
    fft_mag    = np.mean(fft_matrix, axis=0)

    centro = len(fft_mag) // 2
    fft_mag[centro - 8: centro + 8] = 0.0

    return fft_mag


async def bucle_deteccion():
    """
    Corre el HackRF en un hilo separado para no bloquear el event loop.
    Actualiza el dict `estado` en cada iteración.
    """
    global estado

    loop = asyncio.get_event_loop()

    proceso = await loop.run_in_executor(
        None,
        lambda: subprocess.Popen(
            ["hackrf_transfer",
             "-f", str(FRECUENCIA_CENTRAL),
             "-s", str(SAMPLE_RATE),
             "-r", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=TAMANO_BLOQUE * 2
        )
    )

    score         = 0.0
    alerta_activa = False
    t_ultimo      = time.time()
    n_bloques     = 0

    try:
        while True:
            # Leer bloque sin bloquear el event loop
            raw = await loop.run_in_executor(
                None, proceso.stdout.read, TAMANO_BLOQUE * 2
            )

            fft_mag = procesar_espectro(raw)
            if fft_mag is None:
                break

            t_ahora  = time.time()
            dt       = t_ahora - t_ultimo
            t_ultimo = t_ahora
            n_bloques += 1

            # ── Lógica original intacta ───────────────────────────────
            piso_ruido_instantaneo = float(np.median(fft_mag[fft_mag > 0]))
            umbral_local           = piso_ruido_instantaneo * MULTIPLICADOR_UMBRAL
            bins_activos           = int(np.sum(fft_mag > umbral_local))

            score = max(0.0, score - DECAY_RATE * dt)

            if bins_activos >= BINS_ACTIVOS_MIN:
                score = min(SCORE_MAXIMO, score + PUNTOS_POR_IMPACTO)

            if not alerta_activa and score >= UMBRAL_SCORE_ACTIVACION:
                alerta_activa = True
            elif alerta_activa and score < UMBRAL_SCORE_LIBERACION:
                alerta_activa = False
            # ─────────────────────────────────────────────────────────

            # Submuestreo del espectro para no saturar la red:
            # enviamos 256 puntos en lugar de 1024
            fft_reducida = fft_mag[::4].tolist()
            fft_max      = max(fft_reducida) if fft_reducida else 1.0
            fft_norm     = [round(v / fft_max, 4) for v in fft_reducida]

            estado = {
                "score":          round(score, 3),
                "score_max":      SCORE_MAXIMO,
                "alerta_activa":  alerta_activa,
                "bins_activos":   bins_activos,
                "bins_min":       BINS_ACTIVOS_MIN,
                "piso_ruido":     round(piso_ruido_instantaneo, 5),
                "umbral_local":   round(umbral_local, 5),
                "fft_norm":       fft_norm,
                "n_bloques":      n_bloques,
                "timestamp":      round(t_ahora, 3),
            }

            # Broadcast a todos los clientes conectados
            if clientes_conectados:
                mensaje = json.dumps(estado)
                await asyncio.gather(
                    *[ws.send(mensaje) for ws in clientes_conectados],
                    return_exceptions=True
                )

            # Cede el control al event loop brevemente
            await asyncio.sleep(0)

    except asyncio.CancelledError:
        pass
    finally:
        proceso.terminate()
        proceso.wait()


# ══════════════════════════════════════════════════════════════
#  MANEJADOR DE CONEXIONES WEBSOCKET
# ══════════════════════════════════════════════════════════════

async def manejador(websocket):
    clientes_conectados.add(websocket)
    print(f"  ✅ Cliente conectado    — total: {len(clientes_conectados)}")
    try:
        await websocket.wait_closed()
    finally:
        clientes_conectados.discard(websocket)
        print(f"  ❌ Cliente desconectado — total: {len(clientes_conectados)}")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║   📡   Servidor WebSocket — Detector de Drones       ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"\n  WebSocket : ws://localhost:8765")
    print(f"  Abre      : detector.html en tu navegador\n")

    async with serve(manejador, "localhost", 8765):
        await bucle_deteccion()


if __name__ == "__main__":
    asyncio.run(main())