import serial
import RPi.GPIO as GPIO
import time
import json
import logging
from datetime import datetime
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# ==============================================================================
# CONFIGURACIÓN GENERAL Y CREDENCIALES
# ==============================================================================

MQTT_USER = "medicinas_gateway"     
MQTT_PASSWORD = "pru123"     
MQTT_BROKER = "localhost"                 
MQTT_PORT = 1883
CLIENT_ID = "raspberry_gateway"

TOPIC_TELEMETRIA = "casa/monitorizacion/gateway/telemetria"
TOPIC_ALARMAS = "casa/monitorizacion/gateway/alarmas"          
TOPIC_SUB_ALL = "casa/monitorizacion/gateway/#"

QOS = 1  
# ------------------------------------------------------------------------------
# CONFIGURACIÓN DE PINES GPIO
# ------------------------------------------------------------------------------
MODO_GPIO = GPIO.BOARD
PIN_BUZZER = 3

PUERTO_SERIE = "/dev/ttyACM0" 
BAUDIOS = 9600

UMBRAL_DISTANCIA = 50
TIEMPO_MAX_DISTANCIA = 20 * 60  # 20 minutos en segundos

INTERVALO_TELEMETRIA = 5        
INTERVALO_CONSOLA = 30       

# ==============================================================================
# LOGGING (para tener trazabilidad de errores de conexión, requisito obligatorio)
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("gateway.log")
    ]
)
log = logging.getLogger("gateway")

# ==============================================================================
# CLASE DE ESTADO DEL SISTEMA
# ==============================================================================
class EstadoSistema:
    def __init__(self):
        self.alarmas = ["08:00", "14:00", "22:00"]
        self.tomas_realizadas = []
        self.alarma_sonando_id = None
        self.distancia_actual = 0
        self.alarma_sonando = False
        self.modo_noche = False
        self.tiempo_inicio_distancia = None
        self.alerta_distancia_enviada = False
        self.mensaje_alerta_display = "Sistema iniciado correctamente"
        self.ultimo_envio_mqtt = 0
        self.ultimo_volcado_consola = 0
        self.mqtt_conectado = False
        self.ultimo_dia_revisado = datetime.now().day

estado = EstadoSistema()

# ==============================================================================
# FUNCIONES DE HARDWARE (BUZZER Y GPIO)
# ==============================================================================
def inicializar_gpio():
    try:
        GPIO.cleanup()
        GPIO.setmode(MODO_GPIO)
        GPIO.setup(PIN_BUZZER, GPIO.OUT)
        GPIO.output(PIN_BUZZER, GPIO.LOW)
        log.info(f"GPIO configurado. Pin Buzzer asignado: {PIN_BUZZER}")
    except Exception as e:
        log.error(f"No se pudo inicializar GPIO: {e}")

def encender_buzzer():
    GPIO.output(PIN_BUZZER, GPIO.HIGH)

def apagar_buzzer():
    GPIO.output(PIN_BUZZER, GPIO.LOW)

def probar_hardware_inicial():
    log.info("Test inicial de Buzzer (0.5s)...")
    encender_buzzer()
    time.sleep(0.5)
    apagar_buzzer()

# ==============================================================================
# UTILIDADES DE ALARMAS
# ==============================================================================
def _es_hora_valida(texto):
    try:
        partes = texto.split(":")
        if len(partes) != 2:
            return False
        h, m = int(partes[0]), int(partes[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, AttributeError):
        return False

def _normalizar_hora(texto):
    h, m = texto.split(":")
    return f"{int(h):02d}:{int(m):02d}"

def calcular_proxima_alarma(ahora):
    if not estado.alarmas:
        return None
    ahora_str = ahora.strftime("%H:%M")
    alarmas_ordenadas = sorted(estado.alarmas)
    pendientes_hoy = [a for a in alarmas_ordenadas if a > ahora_str]
    if pendientes_hoy:
        return pendientes_hoy[0]
    return alarmas_ordenadas[0]

def evaluar_alarmas(ahora):
    ahora_str = ahora.strftime("%H:%M")
    if ahora_str in estado.alarmas and ahora_str not in estado.tomas_realizadas:
        if estado.alarma_sonando_id != ahora_str:
            estado.alarma_sonando_id = ahora_str
            estado.alarma_sonando = True
            estado.mensaje_alerta_display = f"¡ALARMA! Medicina no tomada ({ahora_str})"
            log.info(estado.mensaje_alerta_display)
            encender_buzzer()

def marcar_toma_actual(ahora):
    ahora_str = ahora.strftime("%H:%M")
    if estado.alarma_sonando_id:
        alarma_objetivo = estado.alarma_sonando_id
    else:
        pendientes_vencidas = sorted(
            a for a in estado.alarmas
            if a <= ahora_str and a not in estado.tomas_realizadas
        )
        alarma_objetivo = pendientes_vencidas[-1] if pendientes_vencidas else None

    if alarma_objetivo:
        if alarma_objetivo not in estado.tomas_realizadas:
            estado.tomas_realizadas.append(alarma_objetivo)
        estado.mensaje_alerta_display = f"Medicina tomada correctamente ({alarma_objetivo})"
        log.info(estado.mensaje_alerta_display)
    else:
        estado.mensaje_alerta_display = "La medicina ya ha sido tomada (botón amarillo pulsado)"
        log.info(estado.mensaje_alerta_display)

    estado.alarma_sonando_id = None
    estado.alarma_sonando = False
    apagar_buzzer()

def reiniciar_tomas_si_es_medianoche(ahora):
    if ahora.day != estado.ultimo_dia_revisado:
        estado.tomas_realizadas = []
        estado.alarma_sonando_id = None
        estado.alarma_sonando = False
        estado.ultimo_dia_revisado = ahora.day
        log.info("Nuevo día: reinicio de medicinas tomadas.")

# ==============================================================================
# FUNCIONES MQTT (con control de errores de conexión)
# ==============================================================================
def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        estado.mqtt_conectado = True
        log.info("Conectado al bróker Mosquitto local.")
        client.subscribe([(TOPIC_SUB_ALL, QOS)])
        log.info(f"Suscrito a: {TOPIC_SUB_ALL}")
    else:
        estado.mqtt_conectado = False
        log.error(f"Fallo de conexión MQTT. reason_code={reason_code}")

def on_disconnect(client, userdata, disconnect_flags=None, reason_code=0, properties=None):
    estado.mqtt_conectado = False
    log.warning(f"Desconectado del bróker (reason_code={reason_code}). "
                f"paho-mqtt reintentará automáticamente con backoff...")

def on_message(client, userdata, msg):
    topic = msg.topic
    raw_payload = msg.payload.decode('utf-8', errors='ignore').strip()
    log.info(f"Mensaje recibido en '{topic}': {raw_payload}")
    procesar_comando(topic, raw_payload)

def procesar_comando(topic, payload_str):
    try:
        if topic == TOPIC_ALARMAS:
            lista = json.loads(payload_str)
            if not isinstance(lista, list):
                raise ValueError("Se esperaba una lista JSON de horas")

            validas = sorted({_normalizar_hora(h) for h in lista if _es_hora_valida(h)})
            estado.alarmas = validas
            # Si una alarma desaparece de la lista, ya no tiene sentido conservarla
            # como "tomada"; limpiamos tomas que ya no correspondan a ninguna alarma.
            estado.tomas_realizadas = [t for t in estado.tomas_realizadas if t in validas]
            if estado.alarma_sonando_id not in validas:
                estado.alarma_sonando_id = None
                estado.alarma_sonando = False
                apagar_buzzer()
            log.info(f"Alarmas actualizadas: {estado.alarmas}")
    except Exception as e:
        log.error(f"No se pudo procesar el comando '{payload_str}' en '{topic}': {e}")

def crear_cliente_mqtt():
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    # Backoff de reconexión: 1s -> 30s. Esto es el control de errores de conexión MQTT.
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client

def conectar_mqtt_con_reintentos(client):
    intentos = 0
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            client.loop_start()
            log.info("Conexión inicial al bróker MQTT establecida.")
            return
        except Exception as e:
            intentos += 1
            espera = min(30, 2 ** intentos)
            log.error(f"No se pudo conectar al bróker (intento {intentos}): {e}. "
                      f"Reintentando en {espera}s...")
            time.sleep(espera)

# ==============================================================================
# LÓGICA DE CONTROL DEL SISTEMA (SENSOR DE DISTANCIA / MODO NOCHE)
# ==============================================================================
def evaluar_sensor_distancia():
    if estado.modo_noche:
        if estado.distancia_actual > UMBRAL_DISTANCIA:
            if estado.tiempo_inicio_distancia is None:
                estado.tiempo_inicio_distancia = time.time()
            transcurrido = time.time() - estado.tiempo_inicio_distancia
            if transcurrido >= TIEMPO_MAX_DISTANCIA and not estado.alerta_distancia_enviada:
                estado.mensaje_alerta_display = f"¡ALERTA! Distancia ({estado.distancia_actual}cm) > {UMBRAL_DISTANCIA}cm"
                log.info(estado.mensaje_alerta_display)
                encender_buzzer()
                estado.alerta_distancia_enviada = True
        else:
            estado.tiempo_inicio_distancia = None
            if estado.alerta_distancia_enviada:
                estado.alerta_distancia_enviada = False
                if not estado.alarma_sonando:
                    apagar_buzzer()

def procesar_mensajes_arduino(linea, ahora):
    log.debug(f"Arduino -> '{linea}'")

    if linea == "AMARILLO_PULSADO":
        marcar_toma_actual(ahora)

    elif linea == "ROJO_PULSADO":
        estado.modo_noche = not estado.modo_noche
        estado_str = "ACTIVADO" if estado.modo_noche else "DESACTIVADO"
        estado.mensaje_alerta_display = f"Modo noche: {estado_str}"
        log.info(f"Botón Rojo Pulsado (Modo noche: {estado_str})")
        if not estado.modo_noche:
            estado.tiempo_inicio_distancia = None
            if estado.alerta_distancia_enviada:
                estado.alerta_distancia_enviada = False
                if not estado.alarma_sonando:
                    apagar_buzzer()

    elif linea.startswith("DIST:"):
        try:
            estado.distancia_actual = int(linea.split(':')[1])
        except (IndexError, ValueError):
            log.warning(f"Línea de distancia mal formada: '{linea}'")

def enviar_telemetria(client, ahora):
    telemetria = {
        "distancia_cm": estado.distancia_actual,
        "modo_noche": estado.modo_noche,
        "alarma_activa": estado.alarma_sonando,
        "alarmas": estado.alarmas,
        "proxima_alarma": calcular_proxima_alarma(ahora),
        "tomas_realizadas": estado.tomas_realizadas,
        "mensaje_estado": estado.mensaje_alerta_display,
    }
    payload_json = json.dumps(telemetria)

    if estado.mqtt_conectado:
        try:
            client.publish(TOPIC_TELEMETRIA, payload_json, qos=QOS)
        except Exception as e:
            log.error(f"Error al publicar telemetría: {e}")
    else:
        log.warning("No se envía telemetría: sin conexión al bróker en este momento.")

    estado.ultimo_envio_mqtt = time.time()

def imprimir_estado_consola(ahora):
    proxima = calcular_proxima_alarma(ahora)
    print("=" * 50)
    print("ESTADO GENERAL DEL SISTEMA")
    print("=" * 50)
    print(f"\nHora actual      : {ahora.strftime('%H:%M:%S')}\n")

    print("Alarmas:")
    if estado.alarmas:
        for a in sorted(estado.alarmas):
            print(f" - {a}")
    else:
        print(" (ninguna configurada)")

    print(f"\nPróxima alarma   : {proxima if proxima else '(ninguna)'}\n")

    print("Tomas realizadas:")
    if estado.tomas_realizadas:
        for t in sorted(estado.tomas_realizadas):
            print(f" - {t}")
    else:
        print(" (ninguna todavía)")

    print(f"\nModo noche       : {'ACTIVO' if estado.modo_noche else 'INACTIVO'}\n")
    print(f"Distancia actual : {estado.distancia_actual} cm\n")
    print(f"MQTT             : {'CONECTADO' if estado.mqtt_conectado else 'DESCONECTADO'}\n")
    print("Estado:")
    print(estado.mensaje_alerta_display)
    print("=" * 50)

    estado.ultimo_volcado_consola = time.time()

def abrir_puerto_serie():
    try:
        arduino = serial.Serial(PUERTO_SERIE, BAUDIOS, timeout=1)
        time.sleep(2)
        log.info(f"Arduino conectado en {PUERTO_SERIE}.")
        return arduino
    except serial.SerialException as e:
        log.error(f"No se pudo abrir el puerto serie {PUERTO_SERIE}: {e}")
        return None

# ==============================================================================
# FUNCIÓN PRINCIPAL (MAIN)
# ==============================================================================
def main():
    log.info("=== INICIANDO RASPBERRY PI) ===")

    inicializar_gpio()
    probar_hardware_inicial()

    mqtt_client = crear_cliente_mqtt()
    conectar_mqtt_con_reintentos(mqtt_client)

    arduino = None
    ultimo_intento_serie = 0

    try:
        while True:
            ahora = datetime.now()

            # Reintento de conexión serie si se desconecta, cada 2s (no bloqueante)
            if (arduino is None or not arduino.is_open) and (time.time() - ultimo_intento_serie > 2):
                arduino = abrir_puerto_serie()
                ultimo_intento_serie = time.time()

            reiniciar_tomas_si_es_medianoche(ahora)
            evaluar_alarmas(ahora)
            evaluar_sensor_distancia()

            if arduino and arduino.is_open:
                try:
                    if arduino.in_waiting > 0:
                        linea = arduino.readline().decode('utf-8', errors='ignore').strip()
                        if linea:
                            procesar_mensajes_arduino(linea, ahora)
                except (serial.SerialException, OSError) as e:
                    log.error(f"Error leyendo el puerto serie, se reintentará conexión: {e}")
                    try:
                        arduino.close()
                    except Exception:
                        pass
                    arduino = None

            if time.time() - estado.ultimo_envio_mqtt >= INTERVALO_TELEMETRIA:
                enviar_telemetria(mqtt_client, ahora)

            if time.time() - estado.ultimo_volcado_consola >= INTERVALO_CONSOLA:
                imprimir_estado_consola(ahora)

            time.sleep(0.05)

    except KeyboardInterrupt:
        log.info("Detención solicitada por el usuario.")

    finally:
        log.info("Cerrando conexiones y liberando pines...")
        apagar_buzzer()
        GPIO.cleanup()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        if arduino and arduino.is_open:
            arduino.close()
        log.info("Programa finalizado correctamente.")

if __name__ == "__main__":
    main()