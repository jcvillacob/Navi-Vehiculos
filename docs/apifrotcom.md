import requests
import pandas as pd
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
BASE_URL = "https://v2api.frotcom.com"
USERNAME = "julian.sierra"
PASSWORD = "Navitrans.2025"

# Fecha de fin: 31 de Marzo de 2026
FECHA_FIN_GLOBAL = "2026-01-31"

# Configuración: {ID: (Placa, Día de Inicio)}
# *** CAMBIO AQUÍ: He puesto "1" en todos para que inicien el 1 de Marzo ***
VEHICULOS_CONFIG = {
    386804: ("PXK375", 1),
    386805: ("PXK371", 1),
    386806: ("PXK376", 1),
    386864: ("PXK374", 1),
    387145: ("PXK367", 1)
}

def get_access_token():
    url = f"{BASE_URL}/v2/authorize"
    payload = {"provider": "frotcom", "username": USERNAME, "password": PASSWORD}
    try:
        response = requests.post(url, data=payload)
        return response.json().get("token") if response.status_code == 201 else None
    except:
        return None

def get_summary_data(token, vehicle_id, df, dt):
    url = f"{BASE_URL}/v2/vehicles/{vehicle_id}/mileageandtime"
    params = {"api_key": token, "df": df, "dt": dt}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else None
    except:
        return None

def get_can_day_records(token, vehicle_id, date_str):
    url = f"{BASE_URL}/v2/vehicles/{vehicle_id}/vehicleCanInfo"
    params = {"api_key": token, "date": date_str}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            validos = [d for d in data if d.get('odometer', 0) > 0]
            return sorted(validos, key=lambda x: x['date'])
        return []
    except:
        return []

def find_valid_reading(token, v_id, start_date, end_date, buscar_inicio=True):
    current = start_date if buscar_inicio else end_date
    limit = end_date if buscar_inicio else start_date
    max_days = abs((end_date - start_date).days) + 2
    attempts = 0

    while attempts <= max_days:
        if (buscar_inicio and current > limit) or (not buscar_inicio and current < limit):
            break
        date_str = current.strftime("%Y-%m-%d")
        records = get_can_day_records(token, v_id, date_str)
        if records:
            dato = records[0] if buscar_inicio else records[-1]
            return dato, current
        current = current + timedelta(days=1) if buscar_inicio else current - timedelta(days=1)
        attempts += 1
    return None, None

if __name__ == "__main__":
    print("Iniciando proceso - Reporte completo Febrero 2026 (1 al 28)...")
    token = get_access_token()
    datos_para_excel = []

    if token:
        f_fin_obj = datetime.strptime(FECHA_FIN_GLOBAL, "%Y-%m-%d")

        for v_id, (placa, dia_inicio) in VEHICULOS_CONFIG.items():
            # Forzamos el año 2026 y el mes 3 (Marzo).
            # El día_inicio ahora viene como 1 desde la configuración.
            f_ini_obj = datetime(2026, 3, dia_inicio)
            
            print(f"Procesando {placa} (Iniciando desde el {f_ini_obj.strftime('%d-%m-%Y')})...")
            
            df_iso = f_ini_obj.replace(hour=0, minute=0, second=0).isoformat()
            dt_iso = f_fin_obj.replace(hour=23, minute=59, second=59).isoformat()

            # Consultas
            resumen = get_summary_data(token, v_id, df_iso, dt_iso)
            reg_ini, f_real_ini = find_valid_reading(token, v_id, f_ini_obj, f_fin_obj, True)
            reg_fin, f_real_fin = find_valid_reading(token, v_id, f_ini_obj, f_fin_obj, False)

            # Conversión de tiempo a horas decimales
            horas_decimales = 0.0
            if resumen:
                segundos_totales = resumen.get('drivingTimeSeconds', 0)
                horas_decimales = round(segundos_totales / 3600, 2)

            odo_ini = reg_ini.get('odometer', 0) if reg_ini else 0
            odo_fin = reg_fin.get('odometer', 0) if reg_fin else 0
            comb_ini = reg_ini.get('totalFuelUsed', 0) if reg_ini else 0
            comb_fin = reg_fin.get('totalFuelUsed', 0) if reg_fin else 0

            datos_para_excel.append({
                "Placa": placa,
                "Fecha Inicio Real": f_real_ini.strftime("%Y-%m-%d") if f_real_ini else "N/A",
                "Fecha Fin Real": f_real_fin.strftime("%Y-%m-%d") if f_real_fin else "N/A",
                "Odómetro Inicial": odo_ini,
                "Odómetro Final": odo_fin,
                "Kms Recorridos": round(odo_fin - odo_ini, 2),
                "Kms GPS": resumen.get('mileageGpsKms', 0) if resumen else 0,
                "Horas Conducción": horas_decimales,
                "Combustible Inicial": comb_ini,
                "Combustible Final": comb_fin,
                "Consumo Total (L)": round(comb_fin - comb_ini, 2)
            })

        if datos_para_excel:
            df = pd.DataFrame(datos_para_excel)
            archivo = "Reporte_Flota_Enero2_Completo_2026.xlsx"
            df.to_excel(archivo, index=False)
            print(f"\n¡Éxito! Reporte generado: {archivo}")
            print(df[["Placa", "Fecha Inicio Real", "Horas Conducción"]])
    else:
        print("Error en la autenticación.")