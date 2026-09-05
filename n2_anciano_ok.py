import json
import time
import tkinter as tk
from datetime import datetime
from threading import Lock

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------
MQTT_BROKER = "192.168.1.88"  
MQTT_PORT = 1883
MQTT_USER = "medicinas_gateway"   
MQTT_PASSWORD = "pru123"
CLIENT_ID = "dashboard_persona_cuidada"

TOPIC_TELEMETRIA = "casa/monitorizacion/gateway/telemetria"
TOPIC_MENSAJE_CUIDADOR = "casa/monitorizacion/cuidador/mensaje"
TOPIC_PREGUNTA_CUIDADOR = "casa/monitorizacion/cuidador/pregunta"
TOPIC_ESTADO_PERSONA = "casa/monitorizacion/persona/estado"

# Colores de modo noche
COLOR_NOCHE_FONDO = "#000e8b"
COLOR_NOCHE_TEXTO = "white"

# ---------------------------------------------------------------------------
estado_lock = Lock()
estado = {
    "alarmas": [],
    "tomas_realizadas": [],
    "proxima_alarma": None,
    "alarma_activa": False,
    "modo_noche": False,
    "ultimo_mensaje": "",
    "conectado": False,
    "pregunta_pendiente": False,
}


# ---------------------------------------------------------------------------
# MQTT (con reconexion automatica, control de errores)
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, reason_code, properties=None):
    with estado_lock:
        estado["conectado"] = (reason_code == 0)
    if reason_code == 0:
        client.subscribe([
            (TOPIC_TELEMETRIA, 1),
            (TOPIC_MENSAJE_CUIDADOR, 1),
            (TOPIC_PREGUNTA_CUIDADOR, 1),
        ])
        print("[MQTT] Conectado y suscrito.")
    else:
        print(f"[MQTT] Fallo de conexion, reason_code={reason_code}")


def on_disconnect(client, userdata, disconnect_flags=None, reason_code=0, properties=None):
    with estado_lock:
        estado["conectado"] = False
    print("[MQTT] Desconectado, se reintentará en unos segundos...")


def on_message(client, userdata, msg):
    try:
        if msg.topic == TOPIC_TELEMETRIA:
            datos = json.loads(msg.payload.decode("utf-8"))
            with estado_lock:
                estado["alarmas"] = datos.get("alarmas", [])
                estado["tomas_realizadas"] = datos.get("tomas_realizadas", [])
                estado["proxima_alarma"] = datos.get("proxima_alarma")
                estado["alarma_activa"] = datos.get("alarma_activa", False)
                estado["modo_noche"] = datos.get("modo_noche", False)

        elif msg.topic == TOPIC_MENSAJE_CUIDADOR:
            texto = msg.payload.decode("utf-8")
            with estado_lock:
                estado["ultimo_mensaje"] = texto

        elif msg.topic == TOPIC_PREGUNTA_CUIDADOR:
            with estado_lock:
                estado["pregunta_pendiente"] = True

    except Exception as e:
        print(f"[ERROR] No se pudo procesar el mensaje de '{msg.topic}': {e}")


def crear_cliente_mqtt():
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


def conectar_con_reintentos(client):
    intentos = 0
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            client.loop_start()
            return
        except Exception as e:
            intentos += 1
            espera = min(30, 2 ** intentos)
            print(f"[MQTT] No se pudo conectar (intento {intentos}): {e}. Reintento en {espera}s")
            time.sleep(espera)


# ---------------------------------------------------------------------------
# INTERFAZ GRAFICA (letras grandes, pocos elementos)
# ---------------------------------------------------------------------------
class DashboardPersona(tk.Tk):
    def __init__(self, mqtt_client):
        super().__init__()
        self.mqtt_client = mqtt_client
        self.title("Dashboard persona cuidada")
        self.attributes("-fullscreen", False)  
        self.geometry("700x600")

        self._parpadeo_encendido = False

        self.lbl_modo_noche = tk.Label(self, font=("Arial", 16, "bold"))
        self.lbl_modo_noche.pack(pady=(10, 0))

        self.lbl_reloj = tk.Label(self, font=("Arial", 60, "bold"))
        self.lbl_reloj.pack(pady=10)

        self.lbl_medicina = tk.Label(
            self, font=("Arial", 30, "bold"), wraplength=650, justify="center"
        )
        self.lbl_medicina.pack(pady=10, fill="x")

        self.lbl_alarmas = tk.Label(
            self, font=("Arial", 18), wraplength=650, justify="center"
        )
        self.lbl_alarmas.pack(pady=5, fill="x")

        self.lbl_mensaje = tk.Label(
            self, font=("Arial", 24), bg="#eef6ff", wraplength=650, justify="center",
            relief="ridge", bd=2
        )
        self.lbl_mensaje.pack(pady=15, fill="x", padx=20)

        self.lbl_alerta = tk.Label(
            self, text="¿ESTÁS BIEN?\nPULSE EL BOTÓN VERDE",
            font=("Arial", 30, "bold"), fg="white", justify="center"
        )
        
        frame_botones = tk.Frame(self)
        frame_botones.pack(pady=20)

        tk.Button(
            frame_botones, text="Estoy bien", font=("Arial", 24),
            bg="#11b811", width=20, height=2,
            command=self.enviar_estoy_bien
        ).pack(side="left", padx=15)

        self.lbl_estado_conexion = tk.Label(self, font=("Arial", 12))
        self.lbl_estado_conexion.pack(side="bottom", pady=5)

        self.after(200, self.actualizar_pantalla)

    # ESTOY BIEN
    def enviar_estoy_bien(self):
        payload = json.dumps({"estado": "bien", "hora": datetime.now().strftime("%H:%M:%S")})
        try:
            self.mqtt_client.publish(TOPIC_ESTADO_PERSONA, payload)
        except Exception as e:
            print(f"[ERROR] No se pudo enviar el estado: {e}")
        with estado_lock:
            estado["pregunta_pendiente"] = False

    def actualizar_pantalla(self):
        self.lbl_reloj.config(text=datetime.now().strftime("%H:%M:%S"))

        with estado_lock:
            alarmas = list(estado["alarmas"])
            tomas = list(estado["tomas_realizadas"])
            proxima = estado["proxima_alarma"]
            alarma_activa = estado["alarma_activa"]
            modo_noche = estado["modo_noche"]
            mensaje = estado["ultimo_mensaje"]
            conectado = estado["conectado"]
            pregunta_pendiente = estado["pregunta_pendiente"]

        # COLORES MODO
        if modo_noche:
            fondo, texto = COLOR_NOCHE_FONDO, COLOR_NOCHE_TEXTO
            self.lbl_modo_noche.config(
                text="MODO NOCHE ACTIVO", bg=fondo, fg="#ffd966"
            )
        else:
            fondo, texto = "white", "black"
            self.lbl_modo_noche.config(text="", bg=fondo, fg=texto)

        self.configure(bg=fondo)
        for widget in (self.lbl_reloj, self.lbl_alarmas, self.lbl_estado_conexion):
            widget.config(bg=fondo, fg=texto)

        # ALARMA
        if alarma_activa:
            self.lbl_medicina.config(
                text="¡ES HORA DE TU MEDICINA!", fg="white", bg="#c0392b"
            )
        elif proxima:
            self.lbl_medicina.config(
                text=f"Próxima toma: {proxima}", fg=texto, bg=fondo
            )
        else:
            self.lbl_medicina.config(
                text="No hay alarmas configuradas", fg=texto, bg=fondo
            )

        if alarmas:
            partes = []
            for hora in sorted(alarmas):
                marca = "✔" if hora in tomas else "⏳"
                partes.append(f"{hora} {marca}")
            self.lbl_alarmas.config(text="Alarmas de hoy:  " + "   ".join(partes))
        else:
            self.lbl_alarmas.config(text="")

        # MENSAJE CUID
        if mensaje:
            self.lbl_mensaje.config(text=f"{mensaje}")
        else:
            self.lbl_mensaje.config(text="(sin mensajes nuevos)")

        # CHECK IN
        if pregunta_pendiente:
            self.lbl_alerta.pack(pady=15, fill="x", padx=20, before=self.lbl_mensaje)
            self._parpadeo_encendido = not self._parpadeo_encendido
            color = "#c0392b" if self._parpadeo_encendido else "#e67e22"
            self.lbl_alerta.config(bg=color)
        else:
            self.lbl_alerta.pack_forget()

        if not conectado:
            self.title("Panel persona cuidada — SIN CONEXIÓN")
            self.lbl_estado_conexion.config(text="Sin conexión con el sistema")
        else:
            self.title("Panel persona cuidada")
            self.lbl_estado_conexion.config(text="Conectado")

        self.after(1000, self.actualizar_pantalla)


if __name__ == "__main__":
    cliente = crear_cliente_mqtt()
    conectar_con_reintentos(cliente)

    app = DashboardPersona(cliente)
    try:
        app.mainloop()
    finally:
        cliente.loop_stop()
        cliente.disconnect()