# hello.py - Step 1 radio test. Run the SAME file on both Pico 2 W boards.
# Each board says "hello" every few seconds and prints anything it hears.

import time, random, machine, ubinascii
from sx1278 import SX1278

led = machine.Pin("LED", machine.Pin.OUT)
name = ubinascii.hexlify(machine.unique_id()).decode()[-4:]   # short board id

radio = SX1278()          # default pins: see WIRING in STEP1.md
radio.listen()
print("Radio OK. This board is", name, "- listening on 433 MHz")

count = 0
next_send = time.ticks_add(time.ticks_ms(), random.randint(1000, 3000))

while True:
    pkt = radio.receive()
    if pkt:
        data, rssi = pkt
        print("RECEIVED:", data.decode("utf-8", "replace"), "| signal", rssi, "dBm")
        led.toggle()

    if time.ticks_diff(time.ticks_ms(), next_send) >= 0:
        count += 1
        msg = "hello #%d from %s" % (count, name)
        radio.send(msg)
        print("sent:    ", msg)
        # random gap so the two boards don't keep talking over each other
        next_send = time.ticks_add(time.ticks_ms(), random.randint(2500, 4500))

    time.sleep_ms(5)
