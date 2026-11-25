# Step 2: Run the forum over LoRa

You need: both Picos (wired as in step 1), both laptops, and `lora-forum.zip`.
No internet is needed between the laptops. Everything goes through the radios.

## A. Turn each Pico into a "LoRa antenna" (do it on both)

1. Plug the Pico in and open **Thonny**.
2. Open `pico/bridge_main.py` from the zip, then **File > Save as… > Raspberry Pi Pico** and name it **`main.py`**. If it asks, overwrite the old file. Leave `sx1278.py` on the Pico, because the bridge needs it.
3. Press the red **Stop** button. The Shell should print `READY`. (`main.py` runs by itself every time the Pico gets power.)
4. **Quit Thonny completely (⌘Q).** Thonny keeps the USB port busy, and the forum can't talk to the Pico while it's open.

## B. Put the program on both laptops

1. Unzip `lora-forum.zip` somewhere easy, like your Desktop.
2. Open **Terminal** and go into the folder:
   ```
   cd ~/Desktop/lora-forum/laptop
   ```
3. Check Python with `python3 --version`. Any version from 3.8 up works. Nothing else needs installing on a Mac.
   On **Windows**, also run `py -m pip install pyserial` once, and use `py` instead of `python3` below.

## C. Try it without radios first (1 laptop, 1 minute)

```
python3 app.py --sim
```
Open http://127.0.0.1:8001 and http://127.0.0.1:8002 in two browser tabs. Those are two simulated laptops linked by a simulated radio that loses 10% of packets. Create an account in each tab, post in one, and watch it appear in the other within a few seconds. Press **Ctrl+C** in Terminal to stop.

## D. The real thing (2 laptops, 2 radios)

Pick a short name for each laptop: letters and digits only, for example `qanoo` and `zeynep`.

On **Qanoo's laptop**, with the Pico plugged in and Thonny closed:
```
python3 app.py --name qanoo --peer zeynep
```
On **Zeynep's laptop**:
```
python3 app.py --name zeynep --peer qanoo
```
On each laptop, open **http://127.0.0.1:8000**.

- The pill at the top turns **green ("zeynep online")** once the other laptop answers over LoRa.
- Create an account, post a topic, and a few seconds later it shows up on the other laptop with a purple **"via LoRa"** tag.
- Your own post first shows **✓ waiting**. It changes to **✓✓ delivered** once the other laptop confirms it has it.
- The **Live packets** panel on the right shows every NDN Interest (→) and Data (←) going through the radio, with signal strength.

If the program can't find the Pico by itself, give the port:
`--port /dev/cu.usbmodem101` on a Mac, or `--port COM3` on Windows. Thonny's status bar shows the port name.

## E. Demo script

1. Show the two laptops a few metres apart with only the radios connecting them. Turn Wi‑Fi off to make the point.
2. Post a topic on laptop A. Point at the → Interest and ← Data lines in the Live packets panel on B.
3. Comment and vote on B. Watch them arrive on A, and the ✓✓ ticks appear.
4. Unplug one Pico. The pill turns red ("offline"). Post on the other side, then plug the Pico back in and start the app again: everything catches up. That shows that NDN is about names, not connections.

## If something goes wrong

| What you see | Fix |
|---|---|
| `No Pico found` | Plug it in, quit Thonny, or pass `--port`. |
| `Resource busy` / `could not open port` | Thonny (or another app) is still open. Quit it. |
| `Pico did not confirm the transmission` | `main.py` on the Pico isn't the bridge. Redo part A and check that Thonny prints `READY`. |
| Peer stays "offline" | The other laptop's app isn't running, or the `--peer` name doesn't match its `--name` exactly. |
| `Address already in use` | Another copy is already running. Close it, or add `--http-port 8010`. |

Run the tests (optional): `python3 -m unittest discover -s tests -v`
