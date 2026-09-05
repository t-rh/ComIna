// --- DEFINICIÓN DE PINES ARDUINO MEGA ---
const int Trigger = 30;         // Salida Trigger HC-SR04
const int Echo = 31;            // Entrada Echo HC-SR04
const int pinAmarillo = 24;     // Botón Amarillo (PULLUP)
const int pinRojo = 25;         // Botón Rojo (PULLUP)
const int pinLED = 46;          // LED de estado Tx/Rx en Pin 46

// Antirrebote (Debounce) por software
bool estadoAmarilloAnterior = HIGH;
bool estadoRojoAnterior = HIGH; 
unsigned long ultimoDebounceAmarillo = 0;
unsigned long ultimoDebounceRojo = 0;
const unsigned long delayDebounce = 50; 

// Control del LED (1 segundo encendido sin congelar millis)
unsigned long tiempoInicioLED = 0;
bool ledActivo = false;

// Control de medición de distancia
unsigned long ultimoTiempoMedicion = 0;

void encenderLED() {
  digitalWrite(pinLED, HIGH);
  tiempoInicioLED = millis();
  ledActivo = true;
}

void setup() {
  Serial.begin(9600);

  // Configuración de salidas y entradas
  pinMode(Trigger, OUTPUT);           
  pinMode(Echo, INPUT);               
  pinMode(pinLED, OUTPUT);
  digitalWrite(pinLED, LOW);

  // PULLUP interno: El botón se activa cuando se conmuta con GND
  pinMode(pinAmarillo, INPUT_PULLUP);
  pinMode(pinRojo, INPUT_PULLUP);

  digitalWrite(Trigger, LOW);         
}

void loop() {
  unsigned long tiempoActual = millis();

  // 1. Apagar el LED tras transcurrir exactamente 1 segundo (1000 ms)
  if (ledActivo && (tiempoActual - tiempoInicioLED >= 1000)) {
    digitalWrite(pinLED, LOW);
    ledActivo = false;
  }

  // 2. Encender LED si la Raspberry Pi envía datos por el puerto serie
  if (Serial.available() > 0) {
    encenderLED();
    while (Serial.available() > 0) Serial.read(); // Limpiar búfer
  }

  // 3. Lectura Botón Amarillo (Pin 24)
  bool lecturaAmarillo = digitalRead(pinAmarillo);
  if (lecturaAmarillo != estadoAmarilloAnterior) {
    if (tiempoActual - ultimoDebounceAmarillo > delayDebounce) {
      ultimoDebounceAmarillo = tiempoActual;
      estadoAmarilloAnterior = lecturaAmarillo;
      if (lecturaAmarillo == LOW) { 
        Serial.println("AMARILLO_PULSADO");
        encenderLED();
      }
    }
  }

  // 4. Lectura Botón Rojo (Pin 25)
  bool lecturaRojo = digitalRead(pinRojo);
  if (lecturaRojo != estadoRojoAnterior) {
    if (tiempoActual - ultimoDebounceRojo > delayDebounce) {
      ultimoDebounceRojo = tiempoActual;
      estadoRojoAnterior = lecturaRojo;
      if (lecturaRojo == LOW) { 
        Serial.println("ROJO_PULSADO");
        encenderLED();
      }
    }
  }

  // 5. Medir y enviar distancia cada 1 segundo
  if (tiempoActual - ultimoTiempoMedicion >= 1000) {
    ultimoTiempoMedicion = tiempoActual;
    
    digitalWrite(Trigger, HIGH);
    delayMicroseconds(10);
    digitalWrite(Trigger, LOW);
    
    long duracion = pulseIn(Echo, HIGH);
    long distancia = duracion * 0.034 / 2;

    Serial.print("DIST:");
    Serial.println(distancia);
    encenderLED();
  }
}