# ServiceBot – Verkabelung und Ansteuerung

Diese Dokumentation beschreibt den getesteten, funktionierenden Stand des ServiceBot auf einem Raspberry Pi Zero 2 W.

## Grundlagen

- Alle GPIO-Angaben verwenden **BCM-Nummern**, nicht die laufende Nummer des 40-Pin-Headers.
- Raspberry Pi und beide L298N müssen eine gemeinsame Masse (`GND`) haben.
- Die Motoren werden aus der Motorversorgung der L298N gespeist, nicht aus dem Raspberry Pi.
- Die Enable-Eingänge (`ENA`/`ENB`) werden per PWM angesteuert. Eventuelle Enable-Jumper am L298N müssen dafür entfernt sein.
- `PWM = 0 %` bedeutet Stopp, `PWM = 100 %` bedeutet volle Leistung.

## Rad- und Motornummern

Draufsicht, Kamera zeigt nach vorne:

```text
             VORNE / KAMERA

       M2                         M3
   vorne links               vorne rechts


       M1                         M4
   hinten links              hinten rechts
```

Die schrägen Rollenlinien der vier Mecanum-Räder bilden von oben gesehen ein `X` zur Fahrzeugmitte.

## Motor Controller 1

### Kanal A – M1, hinten links

| Signal | BCM-GPIO | Physischer Pin |
|---|---:|---:|
| ENA | GPIO18 | Pin 12 |
| IN1 | GPIO23 | Pin 16 |
| IN2 | GPIO24 | Pin 18 |
| GND | GND | z. B. Pin 14 |

Dieser Motor ist gegenüber der logischen Fahrtrichtung invertiert:

| Gewünschte Bewegung | IN1 / GPIO23 | IN2 / GPIO24 | ENA / GPIO18 |
|---|---|---|---|
| vorwärts | LOW | HIGH | PWM |
| rückwärts | HIGH | LOW | PWM |
| Stopp | LOW | LOW | LOW |

### Kanal B – M2, vorne links

| Signal | BCM-GPIO | Physischer Pin |
|---|---:|---:|
| ENB | GPIO12 | Pin 32 |
| IN3 | GPIO25 | Pin 22 |
| IN4 | GPIO16 | Pin 36 |
| GND | GND | z. B. Pin 14 |

| Gewünschte Bewegung | IN3 / GPIO25 | IN4 / GPIO16 | ENB / GPIO12 |
|---|---|---|---|
| vorwärts | HIGH | LOW | PWM |
| rückwärts | LOW | HIGH | PWM |
| Stopp | LOW | LOW | LOW |

## Motor Controller 2

### Kanal A – M3, vorne rechts

| Signal | BCM-GPIO | Physischer Pin |
|---|---:|---:|
| ENA | GPIO13 | Pin 33 |
| IN1 | GPIO17 | Pin 11 |
| IN2 | GPIO27 | Pin 13 |
| GND | GND | z. B. Pin 34 |

| Gewünschte Bewegung | IN1 / GPIO17 | IN2 / GPIO27 | ENA / GPIO13 |
|---|---|---|---|
| vorwärts | HIGH | LOW | PWM |
| rückwärts | LOW | HIGH | PWM |
| Stopp | LOW | LOW | LOW |

### Kanal B – M4, hinten rechts

| Signal | BCM-GPIO | Physischer Pin |
|---|---:|---:|
| ENB | **GPIO21** | **Pin 40** |
| IN3 | GPIO22 | Pin 15 |
| IN4 | GPIO5 | Pin 29 |
| GND | GND | z. B. Pin 34 |

| Gewünschte Bewegung | IN3 / GPIO22 | IN4 / GPIO5 | ENB / GPIO21 |
|---|---|---|---|
| vorwärts | HIGH | LOW | PWM |
| rückwärts | LOW | HIGH | PWM |
| Stopp | LOW | LOW | LOW |

> GPIO19 wird nicht mehr verwendet. Er ließ sich im aufgebauten System nicht auf HIGH schalten; M4 funktioniert mit GPIO21.

## Mecanum-Fahrmuster

`V` = Rad vorwärts, `R` = Rad rückwärts.

| Taste | Bewegung | M1 hinten links | M2 vorne links | M3 vorne rechts | M4 hinten rechts |
|---|---|---|---|---|---|
| W | vorwärts | V | V | V | V |
| S | rückwärts | R | R | R | R |
| A | links drehen | R | R | V | V |
| D | rechts drehen | V | V | R | R |
| Q | seitlich links | V | R | V | R |
| E | seitlich rechts | R | V | R | V |

Die Angaben in dieser Tabelle sind logische Radrichtungen. Bei M1 setzt die Software diese wegen der invertierten Verkabelung automatisch in die umgekehrten GPIO-Pegel um.

## Kamera

Verwendete Kamera: `OV5647`, Ausgabe `640 × 480` bei `20 fps`.

Das Rohbild steht mechanisch auf dem Kopf und wird deshalb um 180° gedreht. In Picamera2 geschieht das durch horizontales und vertikales Spiegeln:

```python
from libcamera import Transform

config = camera.create_video_configuration(
    main={"size": (640, 480), "format": "RGB888"},
    controls={"FrameRate": 20},
    transform=Transform(hflip=True, vflip=True),
)
```

`hflip=True` und `vflip=True` zusammen entsprechen einer Drehung um 180°.

## Installierte Steuerung

- Weboberfläche: `http://192.168.1.61:8080/`
- Anwendung: `/home/bot/servicebot-control/app.py`
- Systemdienst: `servicebot-control.service`
- Sicherheitsstopp: Motoren stoppen spätestens 0,8 Sekunden nach dem letzten Steuerbefehl.
