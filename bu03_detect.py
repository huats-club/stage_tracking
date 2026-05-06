# Huats Club 2026
import serial
import time

ser = serial.Serial('/dev/serial0', baudrate=115200, timeout=1)
ser.reset_input_buffer()
ser.write(b'AT\r\n')
time.sleep(0.5)

n = ser.in_waiting
if n:
    print(f"Got {n} bytes: {ser.read(n)!r}")
else:
    print("No response")

ser.close()
