import serial
import threading # Allow listening + typing cmds simultaneously


PORT = "/dev/cu.usbserial-0001"
BAUD = 57600 # Speed

radio = serial.Serial(PORT, BAUD, timeout = 2)
print("Laptop is listening...")
while True:
    msg = input("Type a message to communicate with Pi: ")
    radio.write((msg + "\n").encode("utf-8")) # Newline is needed so Pi knows msg is completed 
    
    response = radio.readline().decode("utf-8").strip()
    
    if response:
        print("Pi said: ", response)
    else:
        print("No response was received!")