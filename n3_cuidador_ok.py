import json
import sqlite3
import time
import queue
from datetime import datetime
from threading import Lock

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------
MQTT_BROKER = "192.168.1.88"  
MQTT_PORT = 1883
MQTT_USER = "medicinas_gateway"     
MQTT_PASSWORD = "pru123"
CLIENT_ID = "dashboard_cuidador"

TOPIC_TELEMETRIA = "casa/monitorizacion/gateway/telemetria"
TOPIC_ALARMAS = "casa/monitorizacion/gateway/alarmas"
TOPIC_MENSAJE_CUIDADOR = "casa/monitorizacion/cuidador/mensaje"
TOPIC_PREGUNTA_CUIDADOR = "casa/monitorizacion/cuidador/pregunta"
TOPIC_ESTADO_PERSONA = "casa/monitorizacion/persona/estado"

DB_PATH = "historial_medicinas.db"

# Colores de modo noche
COLOR_NOCHE_FONDO = "#18078b"
COLOR_NOCHE_TEXTO = "white"

# ---------------------------------------------------------------------------
# BASE DE DATOS LOCAL (para poder pintar tendencias con el tiempo)
# ---------------------------------------------------------------------------
def preparar_base_datos():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_distancia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT NOT NULL,
            distancia_cm INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tomas_medicina (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT NOT NULL,
            alarma TEXT
        )
    """)
    con.commit()
    con.close()


def guardar_lectura_distancia(distancia):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO lecturas_distancia (fecha_hora, distancia_cm) VALUES (?, ?)",
        (datetime.now().isoformat(timespec="seconds"), distancia),
    )
    con.commit()
    con.close()


def guardar_toma_medicina(alarma):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO tomas_medicina (fecha_hora, alarma) VALUES (?, ?)",
        (datetime.now().isoformat(timespec="seconds"), alarma),
    )
    con.commit()
    con.close()


def leer_ultimas_distancias(limite=200):
    con = sqlite3.connect(DB_PATH)
    filas = con.execute(
        "SELECT fecha_hora, distancia_cm FROM lecturas_distancia ORDER BY id DESC LIMIT ?",
        (limite,),
    ).fetchall()
    con.close()
    return list(reversed(filas))  # en orden cronologico


def leer_ultimas_tomas(limite=30):
    con = sqlite3.connect(DB_PATH)
    filas = con.execute(
        "SELECT fecha_hora, alarma FROM tomas_medicina ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()
    con.close()
    return filas


# ---------------------------------------------------------------------------
# ESTADO COMPARTIDO ENTRE EL HILO DE MQTT Y LA INTERFAZ
# ---------------------------------------------------------------------------
estado_lock = Lock()
estado_compartido = {
    "distancia_cm": 0,
    "modo_noche": False,
    "alarma_activa": False,
    "alarmas": [],
    "proxima_alarma": None,
    "tomas_realizadas": [],
    "mensaje_estado": "",
    "conectado": False,
}
ultimo_conjunto_tomas = set() 
cola_mensajes_persona = queue.Queue()  


# ---------------------------------------------------------------------------
# MQTT (con reconexion automatica, control de errores)
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, reason_code, properties=None):
    with estado_lock:
        estado_compartido["conectado"] = (reason_code == 0)
    if reason_code == 0:
        client.subscribe([(TOPIC_TELEMETRIA, 1), (TOPIC_ESTADO_PERSONA, 1)])
        print("[MQTT] Conectado y suscrito.")
    else:
        print(f"[MQTT] Fallo de conexion, reason_code={reason_code}")


def on_disconnect(client, userdata, disconnect_flags=None, reason_code=0, properties=None):
    with estado_lock:
        estado_compartido["conectado"] = False
    print("[MQTT] Desconectado, paho-mqtt reintentara solo...")


def on_message(client, userdata, msg):
    global ultimo_conjunto_tomas
    try:
        if msg.topic == TOPIC_TELEMETRIA:
            datos = json.loads(msg.payload.decode("utf-8"))

            distancia = datos.get("distancia_cm")
            if distancia is not None:
                guardar_lectura_distancia(distancia)

            tomas_ahora = set(datos.get("tomas_realizadas", []))
            nuevas = tomas_ahora - ultimo_conjunto_tomas
            for alarma in nuevas:
                guardar_toma_medicina(alarma)
            ultimo_conjunto_tomas = tomas_ahora

            with estado_lock:
                estado_compartido["distancia_cm"] = datos.get("distancia_cm", 0)
                estado_compartido["modo_noche"] = datos.get("modo_noche", False)
                estado_compartido["alarma_activa"] = datos.get("alarma_activa", False)
                estado_compartido["alarmas"] = datos.get("alarmas", [])
                estado_compartido["proxima_alarma"] = datos.get("proxima_alarma")
                estado_compartido["tomas_realizadas"] = datos.get("tomas_realizadas", [])
                estado_compartido["mensaje_estado"] = datos.get("mensaje_estado", "")

        elif msg.topic == TOPIC_ESTADO_PERSONA:
            datos = json.loads(msg.payload.decode("utf-8"))
            texto = f"{datos.get('hora', '')} - {datos.get('estado', '')}"
            cola_mensajes_persona.put(texto)

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
# INTERFAZ GRAFICA
# ---------------------------------------------------------------------------
class DashboardCuidador(tk.Tk):
    def __init__(self, mqtt_client):
        super().__init__()
        self.mqtt_client = mqtt_client
        self.title("Panel del cuidador")
        self.geometry("950x700")

        self.style = ttk.Style(self)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.tab_general = ttk.Frame(self.notebook)
        self.tab_alarmas = ttk.Frame(self.notebook)
        self.tab_mensajes = ttk.Frame(self.notebook)
        self.tab_tendencias = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_general, text="Estado General")
        self.notebook.add(self.tab_alarmas, text="Alarmas")
        self.notebook.add(self.tab_mensajes, text="Mensajes")
        self.notebook.add(self.tab_tendencias, text="Tendencias")

        self._montar_tab_general()
        self._montar_tab_alarmas()
        self._montar_tab_mensajes()
        self._montar_tab_tendencias()

        self.after(1000, self._refrescar_estado_general)
        self.after(1000, self._refrescar_tendencias)
        self.after(500, self._revisar_mensajes_entrantes)

    # ---------------------------------------------------------------
    # TAB 0: Estado General (todo de un vistazo)
    # ---------------------------------------------------------------
    def _montar_tab_general(self):
        contenedor = ttk.Frame(self.tab_general)
        contenedor.pack(fill="both", expand=True, padx=20, pady=20)

        self.lbl_modo_noche_general = tk.Label(contenedor, font=("Arial", 22, "bold"))
        self.lbl_modo_noche_general.pack(pady=(0, 15), fill="x")

        filas = [
            ("proxima_alarma", "Próxima alarma:"),
            ("alarmas_configuradas", "Alarmas configuradas:"),
            ("medicacion_tomada", "Medicación tomada:"),
            ("alarma_activa", "Alarma activa:"),
            ("mqtt", "Conexión MQTT:"),
        ]

        self.labels_estado_general = {}
        grid = ttk.Frame(contenedor)
        grid.pack(fill="x")
        for i, (clave, etiqueta) in enumerate(filas):
            ttk.Label(grid, text=etiqueta, font=("Arial", 14, "bold")).grid(
                row=i, column=0, sticky="e", padx=10, pady=8
            )
            valor = ttk.Label(grid, text="-", font=("Arial", 14))
            valor.grid(row=i, column=1, sticky="w", padx=10, pady=8)
            self.labels_estado_general[clave] = valor

        frame_pregunta = ttk.Frame(contenedor)
        frame_pregunta.pack(pady=40)
        tk.Button(
                    frame_pregunta, text="¿Estás bien?", font=("Arial", 24),
                    bg="#ff4000", width=16, height=2,
                    command=self._preguntar_esta_bien
                ).pack(side="left", padx=15)

        self.lbl_ultima_respuesta = ttk.Label(
            contenedor, text="Última respuesta: (ninguna todavía)", font=("Arial", 12)
        )
        self.lbl_ultima_respuesta.pack(pady=10)

    def _preguntar_esta_bien(self):
        try:
            self.mqtt_client.publish(
                TOPIC_PREGUNTA_CUIDADOR,
                json.dumps({"hora": datetime.now().strftime("%H:%M:%S")}),
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo enviar la pregunta: {e}")

    def _refrescar_estado_general(self):
        with estado_lock:
            snapshot = dict(estado_compartido)

        modo_noche = snapshot["modo_noche"]
        self._aplicar_modo_noche(modo_noche)

        if modo_noche:
            self.lbl_modo_noche_general.config(text="MODO NOCHE ACTIVO")
        else:
            self.lbl_modo_noche_general.config(text="Modo día")

        proxima = snapshot["proxima_alarma"] or "(ninguna)"
        self.labels_estado_general["proxima_alarma"].config(text=proxima)

        alarmas = snapshot["alarmas"]
        self.labels_estado_general["alarmas_configuradas"].config(
            text=", ".join(sorted(alarmas)) if alarmas else "(ninguna)"
        )

        tomas = snapshot["tomas_realizadas"]
        self.labels_estado_general["medicacion_tomada"].config(
            text=f"{len(tomas)} de {len(alarmas)} tomas de hoy"
        )

        self.labels_estado_general["alarma_activa"].config(
            text="SÍ, sonando" if snapshot["alarma_activa"] else "No"
        )

        self.labels_estado_general["mqtt"].config(
            text="CONECTADO" if snapshot["conectado"] else "DESCONECTADO"
        )

        self.after(1000, self._refrescar_estado_general)

    def _aplicar_modo_noche(self, activo):
        if activo:
            fondo, texto = COLOR_NOCHE_FONDO, COLOR_NOCHE_TEXTO
        else:
            fondo, texto = "SystemButtonFace", "black"

        self.configure(bg=fondo)
        try:
            self.style.configure("TFrame", background=fondo)
            self.style.configure("TLabel", background=fondo, foreground=texto)
        except tk.TclError:
            pass
        self.lbl_modo_noche_general.config(bg=fondo, fg="#ffd966" if activo else "black")

    # ---------------------------------------------------------------
    # TAB 1: Tendencias (grafica de distancia + historial de tomas)
    # ---------------------------------------------------------------
    def _montar_tab_tendencias(self):
        contenedor = ttk.Frame(self.tab_tendencias)
        contenedor.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(contenedor, text="Distancia actual:", font=("Arial", 11, "bold")).pack(anchor="w")
        self.lbl_distancia_actual = ttk.Label(contenedor, text="- cm", font=("Arial", 14))
        self.lbl_distancia_actual.pack(anchor="w", pady=(0, 10))

        # GRAF DIST
        self.figura = plt.Figure(figsize=(6, 3.0), dpi=100)
        self.ejes = self.figura.add_subplot(111)
        self.ejes.set_title("Distancia detectada (últimas lecturas)")
        self.ejes.set_ylabel("cm")
        self.canvas_grafica = FigureCanvasTkAgg(self.figura, master=contenedor)
        self.canvas_grafica.get_tk_widget().pack(fill="both", expand=True)

        # HISTORIAL MEDICINAS
        ttk.Label(contenedor, text="Últimas tomas de medicina registradas:",
                  font=("Arial", 11, "bold")).pack(anchor="w", pady=(15, 5))

        self.lista_tomas = tk.Listbox(contenedor, height=8)
        self.lista_tomas.pack(fill="x")

    def _refrescar_tendencias(self):
        with estado_lock:
            distancia = estado_compartido["distancia_cm"]
        self.lbl_distancia_actual.config(text=f"{distancia} cm")
        # GRAF
        filas = leer_ultimas_distancias()
        self.ejes.clear()
        self.ejes.set_title("Distancia detectada (últimas lecturas)")
        self.ejes.set_ylabel("cm")
        if filas:
            horas = [f[0][-8:] for f in filas]  
            valores = [f[1] for f in filas]
            self.ejes.plot(horas, valores, marker="o", markersize=2)
            paso = max(1, len(horas) // 8)
            self.ejes.set_xticks(horas[::paso])
            self.ejes.tick_params(axis="x", rotation=45)
        self.canvas_grafica.draw()
        # MEDICINAS
        self.lista_tomas.delete(0, tk.END)
        for fecha_hora, alarma in leer_ultimas_tomas():
            etiqueta = f"{fecha_hora}" + (f"  (alarma {alarma})" if alarma else "")
            self.lista_tomas.insert(tk.END, etiqueta)

        self.after(5000, self._refrescar_tendencias)  

    # ---------------------------------------------------------------
    # TAB 2: Gestion de alarmas (multiples tomas al dia)
    # ---------------------------------------------------------------
    def _montar_tab_alarmas(self):
        contenedor = ttk.Frame(self.tab_alarmas)
        contenedor.pack(fill="both", expand=True, padx=20, pady=20)

        ttk.Label(contenedor, text="Alarmas de medicación",
                  font=("Arial", 14, "bold")).pack(pady=(0, 15))

        frame_lista = ttk.Frame(contenedor)
        frame_lista.pack(fill="both", expand=True)

        self.lista_alarmas = tk.Listbox(frame_lista, height=10, font=("Arial", 12))
        self.lista_alarmas.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(frame_lista, orient="vertical", command=self.lista_alarmas.yview)
        scrollbar.pack(side="left", fill="y")
        self.lista_alarmas.config(yscrollcommand=scrollbar.set)

        frame_nueva = ttk.Frame(contenedor)
        frame_nueva.pack(pady=15)

        ttk.Label(frame_nueva, text="Hora (0-23):").grid(row=0, column=0, padx=5)
        self.spin_hora = ttk.Spinbox(frame_nueva, from_=0, to=23, width=5)
        self.spin_hora.set(8)
        self.spin_hora.grid(row=0, column=1, padx=5)

        ttk.Label(frame_nueva, text="Minuto (0-59):").grid(row=0, column=2, padx=5)
        self.spin_minuto = ttk.Spinbox(frame_nueva, from_=0, to=59, width=5)
        self.spin_minuto.set(0)
        self.spin_minuto.grid(row=0, column=3, padx=5)

        ttk.Button(frame_nueva, text="➕ Añadir alarma",
                   command=self._anadir_alarma).grid(row=0, column=4, padx=10)

        frame_acciones = ttk.Frame(contenedor)
        frame_acciones.pack(pady=10)

        ttk.Button(frame_acciones, text="Eliminar alarma seleccionada",
                   command=self._eliminar_alarma).pack(padx=5)

        self.lbl_estado_alarmas = ttk.Label(contenedor, text="")
        self.lbl_estado_alarmas.pack(pady=10)
        self.alarmas_locales = []
        self.after(20000, self._sincronizar_lista_alarmas)

    def _sincronizar_lista_alarmas(self):
        with estado_lock:
            alarmas_remotas = list(estado_compartido["alarmas"])
            tomas = set(estado_compartido["tomas_realizadas"])

        if not self.alarmas_locales and alarmas_remotas:
            self.alarmas_locales = sorted(alarmas_remotas)

        self.lista_alarmas.delete(0, tk.END)
        for hora in sorted(self.alarmas_locales):
            marca = " (tomada)" if hora in tomas else " (pendiente)"
            self.lista_alarmas.insert(tk.END, hora + marca)

        self.after(2000, self._sincronizar_lista_alarmas)

    def _anadir_alarma(self):
        try:
            hora = int(self.spin_hora.get())
            minuto = int(self.spin_minuto.get())
            if not (0 <= hora <= 23 and 0 <= minuto <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Hora o minuto fuera de rango.")
            return

        texto_alarma = f"{hora:02d}:{minuto:02d}"
        if texto_alarma in self.alarmas_locales:
            messagebox.showinfo("Aviso", "Esa alarma ya está en la lista.")
            return

        self.alarmas_locales.append(texto_alarma)
        self.alarmas_locales.sort()
        self._enviar_alarmas()

    def _eliminar_alarma(self):
        seleccion = self.lista_alarmas.curselection()
        if not seleccion:
            messagebox.showinfo("Aviso", "Selecciona primero una alarma de la lista.")
            return

        texto = self.lista_alarmas.get(seleccion[0])
        hora = texto.split(" ")[0]
        if hora in self.alarmas_locales:
            self.alarmas_locales.remove(hora)
        self._enviar_alarmas()

    def _enviar_alarmas(self):
        try:
            payload = json.dumps(self.alarmas_locales)
            self.mqtt_client.publish(TOPIC_ALARMAS, payload)
            self.lbl_estado_alarmas.config(
                text=f"Alarmas enviadas: {', '.join(self.alarmas_locales) if self.alarmas_locales else '(ninguna)'}"
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo enviar la lista de alarmas: {e}")

    # ---------------------------------------------------------------
    # TAB 3: Mensajes con la persona cuidada
    # ---------------------------------------------------------------
    def _montar_tab_mensajes(self):
        contenedor = ttk.Frame(self.tab_mensajes)
        contenedor.pack(fill="both", expand=True, padx=10, pady=10)

        frame_botones = ttk.Frame(contenedor)
        frame_botones.pack(pady=10)

        ttk.Button(frame_botones, text="Enviar 'Buenas noches'",
                   command=lambda: self._enviar_mensaje("Buenas noches, que descanses")
                   ).pack(side="left", padx=5)
        ttk.Button(frame_botones, text="Enviar 'Buenos días'",
                   command=lambda: self._enviar_mensaje("Buenos días, ten un buen día")
                   ).pack(side="left", padx=5)

        frame_libre = ttk.Frame(contenedor)
        frame_libre.pack(fill="x", pady=10)
        self.entrada_mensaje = ttk.Entry(frame_libre)
        self.entrada_mensaje.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(frame_libre, text="Enviar",
                   command=lambda: self._enviar_mensaje(self.entrada_mensaje.get())
                   ).pack(side="left")

        ttk.Label(contenedor, text="Respuestas de la persona cuidada:",
                  font=("Arial", 14, "bold")).pack(anchor="w", pady=(20, 5))
        self.lista_respuestas = tk.Listbox(contenedor, height=10)
        self.lista_respuestas.pack(fill="both", expand=True)

    def _enviar_mensaje(self, texto):
        texto = texto.strip()
        if not texto:
            return
        try:
            self.mqtt_client.publish(TOPIC_MENSAJE_CUIDADOR, texto)
            self.entrada_mensaje.delete(0, tk.END)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo enviar el mensaje: {e}")

    def _revisar_mensajes_entrantes(self):
        hubo_nuevas = False
        while not cola_mensajes_persona.empty():
            texto = cola_mensajes_persona.get()
            self.lista_respuestas.insert(0, texto)
            hubo_nuevas = True

        if hubo_nuevas:
            self.lbl_ultima_respuesta.config(text=f"Última respuesta: {self.lista_respuestas.get(0)}")

        self.after(500, self._revisar_mensajes_entrantes)


if __name__ == "__main__":
    preparar_base_datos()

    cliente = crear_cliente_mqtt()
    conectar_con_reintentos(cliente)

    app = DashboardCuidador(cliente)
    try:
        app.mainloop()
    finally:
        cliente.loop_stop()
        cliente.disconnect()