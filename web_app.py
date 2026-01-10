from flask import Flask, render_template, jsonify
import requests
from datetime import datetime, timezone

app = Flask(__name__)

# Stop IDs
CARROLL_F21 = "F21"  # Carroll St
HOYT_A42 = "A42"     # Hoyt-Schermerhorn Sts
LAFAYETTE_D21 = "D21"  # Broadway-Lafayette St

BASE_URL = "https://realtimerail.nyc/transiter/v0.6/systems/us-ny-subway/stops"
CARROLL_TO_LAFAYETTE_TRAVEL_TIME = 8 * 60  # ~8 minutes


def get_arrivals(stop_id):
    """Fetch arrival data for a given stop."""
    url = f"{BASE_URL}/{stop_id}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def parse_arrivals(data, routes=None):
    """Parse arrivals from API response."""
    arrivals = []
    now = datetime.now(timezone.utc).timestamp()

    stop_times = data.get("stopTimes", [])
    for st in stop_times:
        trip = st.get("trip", {})
        route_id = trip.get("route", {}).get("id", "")

        if routes and route_id not in routes:
            continue

        arrival_info = st.get("arrival", {}) or st.get("departure", {})
        arrival_time_str = arrival_info.get("time")
        if arrival_time_str:
            arrival_time = int(arrival_time_str)
            seconds_until = arrival_time - now
            if seconds_until > 0:
                arrivals.append((route_id, seconds_until))

    return sorted(arrivals, key=lambda x: x[1])


def get_next_train(arrivals, routes=None):
    """Get the next train from a list of arrivals."""
    for route, seconds in arrivals:
        if routes is None or route in routes:
            return route, seconds
    return None, None


def get_subway_data():
    """Get all subway data for the dashboard."""
    try:
        carroll_data = get_arrivals(CARROLL_F21)
        hoyt_data = get_arrivals(HOYT_A42)
        lafayette_data = get_arrivals(LAFAYETTE_D21)

        carroll_arrivals = parse_arrivals(carroll_data)
        f_route, f_time = get_next_train(carroll_arrivals, routes=["F"])
        g_route, g_time = get_next_train(carroll_arrivals, routes=["G"])

        # G-Switch logic
        g_switch = None
        if g_time and f_time and (f_time - g_time) >= 6 * 60:
            hoyt_arrivals = parse_arrivals(hoyt_data)
            a_route, a_time = get_next_train(hoyt_arrivals, routes=["A"])
            if a_time:
                g_to_hoyt_time = g_time + 3 * 60
                if a_time <= g_to_hoyt_time + 5 * 60:
                    g_switch = "G to A"

        # B/D Express logic
        bd_express = None
        catchable_bd = []
        if f_time:
            f_at_lafayette = f_time + CARROLL_TO_LAFAYETTE_TRAVEL_TIME
            lafayette_arrivals = parse_arrivals(lafayette_data, routes=["B", "D"])
            
            for route, bd_time in lafayette_arrivals:
                wait = bd_time - f_at_lafayette
                if wait >= 0:
                    viable = wait <= 2 * 60
                    catchable_bd.append({
                        "route": route,
                        "time": bd_time / 60,
                        "wait": wait / 60,
                        "viable": viable
                    })
                    if viable and not bd_express:
                        bd_express = f"Transfer to {route} at Lafayette"
                    if len(catchable_bd) >= 2:
                        break

        # Recommendation
        recommended = "G" if g_switch else "F"

        return {
            "f_time": round(f_time / 60, 1) if f_time else None,
            "g_time": round(g_time / 60, 1) if g_time else None,
            "g_switch": g_switch,
            "bd_express": bd_express,
            "catchable_bd": catchable_bd,
            "f_at_lafayette": round((f_time + CARROLL_TO_LAFAYETTE_TRAVEL_TIME) / 60, 1) if f_time else None,
            "recommended": recommended,
            "last_updated": datetime.now().strftime("%I:%M:%S %p")
        }
    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    return jsonify(get_subway_data())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
