# sx1278.py - minimal LoRa driver for the SX1278 (XL1278-SMT) on a Raspberry Pi Pico 2 W.
# MicroPython. No external libraries needed.
#
# Design note: this class is the only place that knows about SX1278 registers.
# Everything above it just calls send() and receive()  (Single Responsibility).

import time
from machine import Pin, SPI

# Registers
REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_FRF_MSB = 0x06
REG_FRF_MID = 0x07
REG_FRF_LSB = 0x08
REG_PA_CONFIG = 0x09
REG_LNA = 0x0C
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_TX_BASE = 0x0E
REG_FIFO_RX_BASE = 0x0F
REG_FIFO_RX_CURRENT = 0x10
REG_IRQ_FLAGS = 0x12
REG_RX_NB_BYTES = 0x13
REG_PKT_SNR = 0x19
REG_PKT_RSSI = 0x1A
REG_MODEM_CONFIG_1 = 0x1D
REG_MODEM_CONFIG_2 = 0x1E
REG_PREAMBLE_MSB = 0x20
REG_PREAMBLE_LSB = 0x21
REG_PAYLOAD_LENGTH = 0x22
REG_MODEM_CONFIG_3 = 0x26
REG_SYNC_WORD = 0x39
REG_DIO_MAPPING_1 = 0x40
REG_VERSION = 0x42

# Modes
MODE_LORA = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_TX = 0x03
MODE_RX_CONTINUOUS = 0x05

# IRQ flags
IRQ_TX_DONE = 0x08
IRQ_CRC_ERROR = 0x20
IRQ_RX_DONE = 0x40

MAX_PAYLOAD = 255


class SX1278:
    def __init__(self, spi_id=0, sck=18, mosi=19, miso=16, nss=17, rst=20, dio0=21,
                 freq_hz=433_000_000, tx_power=10, sync_word=0x12):
        self.spi = SPI(spi_id, baudrate=1_000_000, polarity=0, phase=0,
                       sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self.nss = Pin(nss, Pin.OUT, value=1)
        self.rst = Pin(rst, Pin.OUT, value=1)
        self.dio0 = Pin(dio0, Pin.IN)
        self._buf1 = bytearray(1)

        self.reset()
        version = self.read(REG_VERSION)
        if version != 0x12:
            raise OSError("SX1278 not found (version=0x%02x, expected 0x12). "
                          "Check 3.3V, GND, SCK, MOSI, MISO, NSS and RST wires." % version)

        self.write(REG_OP_MODE, MODE_LORA | MODE_SLEEP)   # LoRa mode can only be set in sleep
        time.sleep_ms(10)
        self.set_frequency(freq_hz)
        self.write(REG_FIFO_TX_BASE, 0x00)
        self.write(REG_FIFO_RX_BASE, 0x00)
        self.write(REG_LNA, self.read(REG_LNA) | 0x03)    # LNA boost
        self.write(REG_MODEM_CONFIG_1, 0x72)              # BW 125 kHz, CR 4/5, explicit header
        self.write(REG_MODEM_CONFIG_2, 0x74)              # SF7, CRC on
        self.write(REG_MODEM_CONFIG_3, 0x04)              # AGC auto
        self.write(REG_PREAMBLE_MSB, 0x00)
        self.write(REG_PREAMBLE_LSB, 0x08)
        self.write(REG_SYNC_WORD, sync_word)
        self.set_tx_power(tx_power)
        self.write(REG_DIO_MAPPING_1, 0x00)               # DIO0 = RxDone
        self.standby()

    # ---- low level SPI ----
    def read(self, reg):
        self.nss.value(0)
        self.spi.write(bytes([reg & 0x7F]))
        self.spi.readinto(self._buf1)
        self.nss.value(1)
        return self._buf1[0]

    def write(self, reg, value):
        self.nss.value(0)
        self.spi.write(bytes([reg | 0x80, value & 0xFF]))
        self.nss.value(1)

    def reset(self):
        self.rst.value(0)
        time.sleep_ms(10)
        self.rst.value(1)
        time.sleep_ms(10)

    # ---- configuration ----
    def set_frequency(self, freq_hz):
        frf = (freq_hz << 19) // 32_000_000
        self.write(REG_FRF_MSB, (frf >> 16) & 0xFF)
        self.write(REG_FRF_MID, (frf >> 8) & 0xFF)
        self.write(REG_FRF_LSB, frf & 0xFF)

    def set_tx_power(self, dbm):
        dbm = max(2, min(17, dbm))
        self.write(REG_PA_CONFIG, 0x80 | (dbm - 2))   # PA_BOOST pin

    def standby(self):
        self.write(REG_OP_MODE, MODE_LORA | MODE_STDBY)

    def listen(self):
        self.write(REG_OP_MODE, MODE_LORA | MODE_RX_CONTINUOUS)

    # ---- data ----
    def send(self, data, timeout_ms=2000):
        if isinstance(data, str):
            data = data.encode()
        if len(data) > MAX_PAYLOAD:
            raise ValueError("payload too long (%d > %d)" % (len(data), MAX_PAYLOAD))
        self.standby()
        self.write(REG_FIFO_ADDR_PTR, 0x00)
        for b in data:
            self.write(REG_FIFO, b)
        self.write(REG_PAYLOAD_LENGTH, len(data))
        self.write(REG_OP_MODE, MODE_LORA | MODE_TX)
        start = time.ticks_ms()
        while not (self.read(REG_IRQ_FLAGS) & IRQ_TX_DONE):
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                self.write(REG_IRQ_FLAGS, 0xFF)
                self.listen()
                raise OSError("TX timeout")
            time.sleep_ms(1)
        self.write(REG_IRQ_FLAGS, 0xFF)
        self.listen()   # go straight back to listening

    def receive(self):
        """Return (payload_bytes, rssi_dbm) if a packet arrived, else None. Non-blocking."""
        flags = self.read(REG_IRQ_FLAGS)
        if not (flags & IRQ_RX_DONE):
            return None
        self.write(REG_IRQ_FLAGS, 0xFF)
        if flags & IRQ_CRC_ERROR:
            return None
        length = self.read(REG_RX_NB_BYTES)
        self.write(REG_FIFO_ADDR_PTR, self.read(REG_FIFO_RX_CURRENT))
        data = bytes(self.read(REG_FIFO) for _ in range(length))
        rssi = self.read(REG_PKT_RSSI) - 164   # 433 MHz band (LF port)
        return data, rssi
