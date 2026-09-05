# Sistema IoT de recordatorio y supervisión de medicación

Red de 3 nodos MQTT sobre un bróker **Mosquitto propio**, para el recordatorio de la toma de medicación y la supervisión de una persona mayor o dependiente.

Trabajo Final — *Comunicaciones Inalámbricas y Protocolos para el Internet de las Cosas*
Máster Universitario en Industria Conectada — UNED

Continuación del proyecto **Sistema IoT de monitorización de personas mayores** (asignatura *Sistemas Digitales para el Internet de las Cosas*): se reutiliza el mismo hardware y el mismo sketch de Arduino; lo que cambia es la sustitución de Thinger.io por un bróker MQTT propio y la ampliación a una red de 3 nodos.

---

## 1. Arquitectura

```
                        ┌──────────────────────────┐
                        │   Bróker MQTT (Mosquitto) │
                        │   ejecutándose en la      │
                        │   Raspberry Pi Gateway    │
                        └─────────────┬─────────────┘
                                      │ WiFi / LAN
        ┌─────────────────────────────┼─────────────────────────────┐
        │                              │                              │
┌───────▼────────┐          ┌──────────▼──────────┐        ┌──────────▼──────────┐
│  Nodo 1         │          │  Nodo 2               │        │  Nodo 3               │
│  Gateway         │  USB/UART│  Panel persona        │        │  Panel cuidador        │
│  (Arduino Mega    │◄────────►  cuidada (Tkinter)     │        │  (Tkinter + SQLite)    │
│  + Raspberry Pi)  │          │                        │        │                        │
└────────────────┘          └────────────────────┘        └────────────────────┘
```

- **Nodo 1 — Gateway**: Arduino Mega 2560 (sensor HC-SR04, pulsadores amarillo/rojo) conectado por USB a una Raspberry Pi, que aloja además el propio bróker Mosquitto.
- **Nodo 2 — Panel de la persona cuidada**: interfaz simplificada (Tkinter) con reloj, estado de medicación, modo noche y botón "Estoy bien".
- **Nodo 3 — Panel del cuidador**: interfaz con pestañas (estado general, alarmas, mensajes, tendencias) y persistencia local en SQLite.

Ningún nodo se conecta directamente a otro: todos son clientes MQTT del mismo bróker.

---

## 2. Requisitos de hardware

- Arduino Mega 2560
- Sensor ultrasónico HC-SR04
- Pulsador amarillo (confirmación de toma) y pulsador rojo (modo noche)
- LED indicador (opcional, solo depuración)
- Buzzer conectado a la Raspberry Pi
- Raspberry Pi (aloja el bróker y el nodo Gateway) conectada por WiFi/Ethernet a la misma red que los otros dos nodos

---

## 3. Instalación del bróker MQTT (Mosquitto)

Se instala **en la Raspberry Pi del nodo Gateway**.

### 3.1 Instalar Mosquitto y el cliente de línea de comandos

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
```

### 3.2 Activar el servicio para que arranque con el sistema

```bash
sudo systemctl enable mosquitto
sudo systemctl status mosquitto
```

### 3.3 Crear el usuario y la contraseña del gateway

Mosquitto no permite conexiones anónimas en esta instalación; se crea un usuario con `mosquitto_passwd`:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd medicinas_gateway
# introduce la contraseña cuando se solicite (debe coincidir con
# MQTT_PASSWORD en los tres scripts Python)
```

### 3.4 Configurar el listener y la autenticación

Crear (o editar) `/etc/mosquitto/conf.d/local.conf`:

```conf
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
```

> `0.0.0.0` hace que el bróker acepte conexiones desde cualquier IP de la red local (necesario para que los paneles de la persona cuidada y del cuidador, en otros equipos, puedan conectarse).

### 3.5 Reiniciar el servicio y comprobar que funciona

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto
```

Prueba rápida en dos terminales de la propia Raspberry Pi:

```bash
# Terminal 1: suscripción de prueba
mosquitto_sub -h localhost -u medicinas_gateway -P <contraseña> -t "casa/medicinas/#" -v

# Terminal 2: publicación de prueba
mosquitto_pub -h localhost -u medicinas_gateway -P <contraseña> -t "casa/medicinas/gateway/telemetria" -m '{"test": true}'
```

Si el mensaje aparece en la Terminal 1, el bróker está operativo.

### 3.6 Averiguar la IP de la Raspberry Pi (para los otros dos nodos)

```bash
hostname -I
```

Esa IP es la que hay que poner como `MQTT_BROKER` en `dashboard_persona.py` y `dashboard_cuidador.py`.

---

## 4. Instalación de dependencias por nodo

### 4.1 Nodo Gateway (Raspberry Pi)

```bash
sudo apt install -y python3-pip python3-serial
pip3 install paho-mqtt RPi.GPIO --break-system-packages
```

Arduino IDE (o `arduino-cli`) para cargar el sketch en el Arduino Mega, usando el mismo `.ino` ya empleado en el trabajo anterior (lectura de HC-SR04, antirrebote de pulsadores y envío por puerto serie de `DIST:`, `AMARILLO_PULSADO`, `ROJO_PULSADO`).

### 4.2 Nodo 2 — Panel de la persona cuidada

Puede ejecutarse en la propia Raspberry Pi o en otro equipo de la misma red con Python 3 y entorno gráfico:

```bash
sudo apt install -y python3-tk
pip3 install paho-mqtt --break-system-packages
```

### 4.3 Nodo 3 — Panel del cuidador

```bash
sudo apt install -y python3-tk
pip3 install paho-mqtt matplotlib --break-system-packages
```

`sqlite3` forma parte de la librería estándar de Python; no requiere instalación adicional.

---

## 5. Configuración de credenciales en el código

En **cada uno de los tres scripts** hay que revisar/ajustar al principio del fichero:

```python
MQTT_USER = "medicinas_gateway"
MQTT_PASSWORD = "<la misma contraseña usada en mosquitto_passwd>"
MQTT_BROKER = "localhost"          # solo en el Gateway; en los otros dos nodos, la IP de la Raspberry Pi
MQTT_PORT = 1883
```

En el Gateway, además:

```python
PUERTO_SERIE = "/dev/ttyACM0"      # ajustar si el Arduino aparece en otro puerto
MODO_GPIO = GPIO.BOARD
PIN_BUZZER = 3
```

---

## 6. Topics MQTT

Prefijo común: `casa/monitorizacion/`

| Topic | Publica | Se suscribe | QoS | Contenido |
|---|---|---|---|---|
| `gateway/telemetria` | Gateway | Persona, Cuidador | 1 | JSON con `distancia_cm`, `modo_noche`, `alarma_activa`, `alarmas`, `proxima_alarma`, `tomas_realizadas`, `mensaje_estado` |
| `gateway/alarmas` | Cuidador | Gateway | ver nota | Lista JSON de horas `"HH:MM"`, validadas y normalizadas por el Gateway antes de aplicarse |
| `cuidador/mensaje` | Cuidador | Persona | ver nota | Texto libre o predefinido enviado a la persona cuidada |
| `cuidador/pregunta` | Cuidador | Persona | ver nota | Aviso de que el cuidador pregunta "¿Estás bien?" |
| `persona/estado` | Persona | Cuidador | ver nota | JSON con hora y estado ("bien") tras pulsar el botón de respuesta |

> **QoS**: el canal de telemetría usa QoS 1 de extremo a extremo (declarado con la constante `QOS = 1` en el Gateway y en la suscripción de los dos paneles). Los canales de `alarmas`, `mensaje`, `pregunta` y `estado` se publican sin especificar `qos` explícito (QoS 0 por defecto); si se quiere homogeneizar, basta con añadir `qos=1` en cada `client.publish(...)` correspondiente.

Los topics `gateway/alarma/hora` y `gateway/alarma/minuto`, presentes en una versión intermedia del proyecto, **se han eliminado**: el sistema de alarma única que representaban quedó sustituido por la lista de varias alarmas de `gateway/alarmas`.

El Gateway se suscribe al comodín `casa/medicinas/gateway/#`, por lo que técnicamente recibe también sus propios mensajes de telemetría; `procesar_comando()` los ignora sin generar error porque solo actúa sobre `TOPIC_ALARMAS`.

---

## 7. Puesta en marcha

Orden recomendado de arranque:

1. **Bróker Mosquitto** (arranca solo como servicio del sistema; comprobar con `systemctl status mosquitto`).
2. **Nodo Gateway**:
   ```bash
   python3 raspberry_gateway.py
   ```
3. **Nodo 2 — Panel de la persona cuidada**:
   ```bash
   python3 dashboard_persona.py
   ```
4. **Nodo 3 — Panel del cuidador**:
   ```bash
   python3 dashboard_cuidador.py
   ```

Para detener cualquiera de los tres nodos: `Ctrl+C`. Los tres liberan sus recursos (GPIO, puerto serie, sesión MQTT) de forma segura al recibir la interrupción.

---

## 8. Estructura del repositorio

```
.
├── README.md
├── gateway/
│   ├── raspberry_gateway.py
│   └── arduino/
│       └── medicinas.ino            # mismo sketch que el trabajo previo (Sistemas Digitales)
├── dashboard_persona/
│   └── dashboard_persona.py
├── dashboard_cuidador/
│   └── dashboard_cuidador.py
│       # crea historial_medicinas.db en local al arrancar
└── docs/
    └── Memoria_TrabajoFinal_ComIna_RamirezHaro.docx
```

---

## 9. Limitaciones conocidas

- Sin cifrado TLS: las credenciales y la telemetría viajan en texto plano por la red local.
- Sin medio inalámbrico alternativo a WiFi (BLE, LoRa, etc.).
- El bróker Mosquitto, al residir en la misma Raspberry Pi que el Gateway, es un punto único de fallo para toda la red.
- El histórico de lecturas y tomas de medicación solo se conserva en el SQLite local del panel del cuidador.
- Si el panel del cuidador envía una lista de alarmas mal formada, el Gateway la descarta silenciosamente (queda registrado en `gateway.log`, pero no se avisa al panel).

Más detalle de estas limitaciones y de las líneas de mejora propuestas, en la memoria (`docs/Memoria_TrabajoFinal_ComIna_RamirezHaro.docx`).

---

## Autora

Teresa Ramírez Haro — Máster Universitario en Industria Conectada, UNED
