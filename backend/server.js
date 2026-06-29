const express = require("express");
const cors = require("cors");
const { SerialPort } = require("serialport");
const { ReadlineParser } = require("@serialport/parser-readline");

const app = express();
app.use(cors());
app.use(express.json());

const PORT = "/dev/cu.usbserial-0001";
const BAUD = 57600;

// Default telemetry structure
let latestTelemetry = {
  type: "telemetry",
  elapsedTime: "00:00:00",
  etaCompletion: "00:00:00",
  battery: 0,
  speed: 0,
  trashCollected: 0,
  waterTemperature: 0,
  gps: {
    latitude: 0,
    longitude: 0,
  },
  progressMeter: 0,
  missionStatus: "NO DATA",
};

const port = new SerialPort({path: PORT, baudRate: BAUD,});

port.on("open", () => {
    console.log("Serial port open");
  });

// Read serial data one at a time
const parser = port.pipe(new ReadlineParser({ delimiter: "\n" }));

parser.on("data", (line) => {
  const cleanLine = line.trim(); 

  if (cleanLine == "") return; // Ignore empty msg

  console.log("Received from LR900-F:", cleanLine);

  try {
    const data = JSON.parse(cleanLine); // Turn data into js obj.

    if (data.type === "telemetry") {
      latestTelemetry = data;
      console.log("Updated telemetry:", latestTelemetry);
    }

  } 
  
  catch (error) {
    console.log("Received non-JSON message:", cleanLine);
  }

});

app.get("/", (req, res) => {
  res.send("Trash Crab backend is running");
});

app.get("/telemetry", (req, res) => {
  res.json(latestTelemetry);
});

app.post("/command/start", (req, res) => {
  port.write("START\n");
  res.json({ status: "START command sent" });
});

app.post("/command/stop", (req, res) => {
  port.write("STOP\n");
  res.json({ status: "STOP command sent" });
});

app.post("/command/return-dock", (req, res) => {
  port.write("RETURN_DOCK\n");
  res.json({ status: "RETURN_DOCK command sent" });
});

app.listen(5001, () => {
  console.log("Trash Crab radio backend running on http://localhost:5001");
});