# bridge_main.py - save this on each Pico as  main.py  (it then starts by itself on power-up).
#
# The Pico is a "dumb antenna" for the laptop: it does not know anything about
# NDN or the forum. It only moves bytes between USB and the LoRa radio.
#
# USB line protocol (text, one command per line):
#   laptop -> Pico :  TX <hex bytes>      send these bytes over LoRa
#                     PING                health check
#   Pico -> laptop :  READY               bridge started
#                     RX <hex> <rssi>     a LoRa packet arrived
#                     OK                  the last TX finished
#                     ERR <message>       something went wrong
#                     PONG                answer to PING

import sys, select, machine, ubinascii
from sx1278 import SX1278

MAX_LINE = 600          # 255 bytes as hex + some slack

led = machine.Pin("LED", machine.Pin.OUT)
radio = SX1278()
radio.listen()

poll = select.poll()
poll.register(sys.stdin, select.POLLIN)


def out(line):
    sys.stdout.write(line + "\n")


def handle(line):
    if line == "PING":
        out("PONG")
    elif line.startswith("TX "):
        try:
            radio.send(ubinascii.unhexlify(line[3:].strip()))
            out("OK")
        except Exception as e:
            out("ERR %s" % e)
    else:
        out("ERR unknown command")


out("READY")
buf = []
while True:
    pkt = radio.receive()
    if pkt:
        data, rssi = pkt
        out("RX %s %d" % (ubinascii.hexlify(data).decode(), rssi))
        led.toggle()

    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch == "\n" or ch == "\r":
            if buf:
                handle("".join(buf).strip())
                buf = []
        else:
            buf.append(ch)
            if len(buf) > MAX_LINE:
                buf = []
                out("ERR line too long")
