from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Literal
import firebase_admin
from firebase_admin import credentials, db
from dotenv import load_dotenv
import time
import os
import pytz
from datetime import datetime

load_dotenv()

# Firebase initialization
cred_dict = {
    "type": os.getenv("google_credentials_type"),
    "project_id": os.getenv("project_id"),
    "private_key_id": os.getenv("private_key_id"),
    "private_key": os.getenv("private_key").replace("\\n", "\n"),
    "client_email": os.getenv("client_email"),
    "client_id": os.getenv("client_id"),
    "auth_uri": os.getenv("auth_uri"),
    "token_uri": os.getenv("token_uri"),
    "auth_provider_x509_cert_url": os.getenv("auth_provider_x509_cert_url"),
    "client_x509_cert_url": os.getenv("client_x509_cert_url"),
    "universe_domain": os.getenv("universe_domain")
}

cred = credentials.Certificate(cred_dict)

# Only initialize if not already initialized (prevents error during reload)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://iot-project-afca0-default-rtdb.firebaseio.com/'
    })

app = FastAPI()

# === Models ===
FilterType = Literal[
    "pre_filter", "sediment_filter", "carbon_filter", "ro_filter", "motor", "alkaline_filter",
]



# === 1. Sensor Metadata Setup ===
class SensorMetadata(BaseModel):
    user_id: str
    filter_type: FilterType
    sensor_id: str
    sensor_name: str

@app.post("/init_sensor/")
def init_sensor(meta: SensorMetadata):
    try:
        path = f"users/{meta.user_id}/{meta.filter_type}/{meta.sensor_id}"
        ref = db.reference(path)
        ref.set({
            "sensor_name": meta.sensor_name,
            "readings": {}
        })
        return {"message": "Sensor initialized successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# === 2. Time-Series Data Upload (Batch for Multiple Filters and Sensors) ===
class SingleReading(BaseModel):
    sensor_id: str
    value: str

class FilterReadings(BaseModel):
    filter_type: FilterType
    readings: List[SingleReading]

class BatchSensorUpload(BaseModel):
    user_id: str
    filters: List[FilterReadings]

# === Background Task Function ===
def process_sensor_data_batch(data: BatchSensorUpload):
    update_data = {}

    # Set time zone
    ist = pytz.timezone('Asia/Kolkata') # Setting the indian time zone
    current_time_stamp = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")  # Getting the current time

    for filter_data in data.filters:
        for reading in filter_data.readings:
            key_path = f"{data.user_id}/{filter_data.filter_type}/{reading.sensor_id}/readings/{current_time_stamp}"
            update_data[key_path] = reading.value

    root_ref = db.reference("users")
    root_ref.update(update_data)

# === API Endpoint ===
@app.post("/upload_batch_sensor_values/")
def upload_batch_sensor_values(data: BatchSensorUpload, background_tasks: BackgroundTasks):
    try:
        background_tasks.add_task(process_sensor_data_batch, data)
        return {"message": "Batch upload started in background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Added deleting data of last 12 hours logic
@app.delete("/delete_last_12_hours/")
def delete_last_12_hours(user_id: str):
    try:
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        twelve_hours_ago = now - timedelta(hours=12)

        user_ref = db.reference(f"users/{user_id}")
        user_data = user_ref.get()

        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")

        for filter_type, sensors in user_data.items():
            for sensor_id, sensor_info in sensors.items():
                readings_ref = db.reference(f"users/{user_id}/{filter_type}/{sensor_id}/readings")
                readings = sensor_info.get("readings", {})

                for timestamp_str in list(readings.keys()):
                    try:
                        # Parse timestamp
                        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ist)
                        if now - timestamp <= timedelta(hours=12):
                            # Reading is within 12 hours; DELETE it
                            readings_ref.child(timestamp_str).delete()
                    except ValueError:
                        # Skip malformed timestamps
                        continue

        return {"message": "Readings from the last 12 hours deleted successfully."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Request you have to made like : DELETE /delete_last_12_hours/?user_id=user123