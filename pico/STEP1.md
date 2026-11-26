# Step 1: Wire the radios and say "hello"

Do this twice, once for each Pico 2 W + XL1278 pair. Goal: two boards that print each other's messages.

## A. Wiring (USB unplugged while wiring)

### A1. Seat the Pico on the breadboard
1. Turn the Pico so the side with the chip and the "Pico 2 W" text faces **up**.
2. Point the **USB port to the top** of the breadboard, at row 1.
3. Line the pins up so the Pico **straddles the groove** down the middle of the breadboard. The left row of pins goes into column **c** and the right row into column **h**. The top pins sit in **row 1** and the bottom pins in **row 20**.
4. Press down gently and evenly until the pins go all the way in.

Every row of 5 holes on one side of the groove is connected inside the breadboard. So a wire plugged into column **i or j** of a row is connected to the Pico pin in that same row on the right side.

### A2. Plug in the LoRa wires (all on the right side, column i or j)

The wire colours are the ones used in our build; any colours work. The second column is the label printed next to each pin on the module.

| Wire color | Module label | Breadboard row (right side) | Pico pin |
|---|---|---|---|
| Orange | VCC (this is the 3.3 V input) | row 5 | pin 36, 3V3(OUT) |
| Black | GND | row 18 | pin 23, GND |
| Green | DIO0 | row 14 | pin 27, GP21 |
| White | RST | row 15 | pin 26, GP20 |
| Gray | MOSI | row 16 | pin 25, GP19 |
| Blue | SCK | row 17 | pin 24, GP18 |
| Yellow | NSS | row 19 | pin 22, GP17 |
| Purple | MISO | row 20 | pin 21, GP16 |

Rules:
- **Never use rows 1 or 2 on the right side.** Those are 5 V pins and will damage the module.
- **Keep the antenna attached** whenever the board has power.
- The unconnected pins on the module (DIO1 to DIO5 and the second GND) stay empty.

## B. Put MicroPython on each Pico (one time)

1. Download the newest `.uf2` file from https://micropython.org/download/RPI_PICO2_W/ (it must be **RPI_PICO2_W**, not RPI_PICO_W).
2. Hold the **BOOTSEL** button, plug the Pico into the computer, then let go. A drive called `RP2350` appears.
3. Drag the `.uf2` onto that drive. It disconnects by itself when it's done.

## C. Install Thonny and copy the files

1. Install Thonny from https://thonny.org.
2. In Thonny, go to **Tools > Options > Interpreter** and choose **MicroPython (Raspberry Pi Pico)**, then select the port that shows up (`/dev/cu.usbmodem...`).
3. Open `sx1278.py`, then **File > Save as... > Raspberry Pi Pico** and save it as `sx1278.py`.
4. Do the same with `hello.py`.
5. Repeat for the second Pico.

## D. Run the test

1. Plug in both Picos. They can go into the same computer or into two laptops.
2. In Thonny, open `hello.py` from the Pico and press **Run** (green arrow).
3. If you only have one Thonny window, power the second Pico from the other laptop or any USB charger. To make it run by itself, save a copy of `hello.py` on it as `main.py`; you can delete that `main.py` afterwards.

What you should see:

```
Radio OK. This board is 3fa1 - listening on 433 MHz
sent:     hello #1 from 3fa1
RECEIVED: hello #1 from 9c07 | signal -45 dBm
```

The green LED on the Pico flips every time it receives a message.

## If it doesn't work

| Message | Fix |
|---|---|
| `SX1278 not found (version=0x00...)` or `0xff` | Wiring problem. Check 3.3V, GND, SCK, MOSI, MISO, NSS, RST. MISO and MOSI swapped is the most common mistake. |
| `TX timeout` | Check the 3.3V and GND wires. The radio may be browning out. |
| Each board sends but never prints `RECEIVED` | Make sure both boards are running and both antennas are on. Keep them at least about 30 cm apart, since very close radios can overload each other. |
| `ImportError: no module named sx1278` | `sx1278.py` wasn't saved onto the Pico. Redo step C.3. |

Once you see `RECEIVED` on both boards, step 1 is done. Send a screenshot or copy the output.
