# Overview

This repository contains my final graduation course work, in Portuguese "Trabalho de Conclusão de Curso" or TCC, that was named REAL-TIME WATER CONSUMPTION MONITORING SYSTEM VIA WEB, in Portuguese "SISTEMA DE MONITORAMENTO EM TEMPO REAL DE CONSUMO DE ÁGUA VIA WEB". It was an end-to-end Internet of Things (IoT) solution for monitoring water consumption, as in the diagram below: 

<p align="center">
  <img width="460" src="https://raw.githubusercontent.com/RomuloDrumond/TCC-iot-agua/master/images/system.png">
</p>


A prototype was developed and installed on a residential faucet, as the images below show it:

<p align="center">
  <img width="460" src="https://raw.githubusercontent.com/RomuloDrumond/TCC-iot-agua/master/images/prototype01.png">
</p>

<p align="center">
  <img width="460" src="https://raw.githubusercontent.com/RomuloDrumond/TCC-iot-agua/master/images/prototype02.png">
</p>

The document in Portuguese can be found on [TCC vFinal.pdf](https://github.com/RomuloDrumond/TCC-iot-agua/raw/master/TCC%20vFinal.pdf).

This work can be divided into three major parts:

* Web app
* IoT device
* Firmware

Each one of them will be presented in more details below

# Web app

A web app was developed using Flask microframework for python. To install dependencies run `pip install -r requirements.txt` on the main directory.

## Built with:

* Flask: web microframework
* celery: for scheduling sending e-mails and some routines
* Flask-SocketIO: to add real-time updating to the webpage using WebSockets
* Flask-SQLAlchemy: ORM for simplifying using different databases


# IoT device
O IoT device was constructed as the schematic below.

<p align="center">
  <img width="460" src="https://raw.githubusercontent.com/RomuloDrumond/TCC-iot-agua/master/images/iot_device.png">
</p>

## Built with:

* Flow sensor SEN-HZ21WA of SAIER
* Arduino Uno
* Wi-fi module ESP8266-01 with an adaptive module to convert 3.3 V logic and power supply to 5 V


# Firmware
As the ESP8266 was used as a modem, the whole firmware code was written on Arduino's language. We can highlight the use of:

* SoftwareSerial library to enable monitoring of the system
* AT commands for communication between Arduino UNO and ESP8266
* HTTP for transferring data over the internet
* JSON data format

The Arduino code can be found on "APÊNDICE A – FIRMWARE DO MEDIDOR INTELIGENTE" of [TCC vFinal.pdf](https://github.com/RomuloDrumond/TCC-iot-agua/raw/master/TCC%20vFinal.pdf).
