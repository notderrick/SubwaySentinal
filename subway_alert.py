import os
import requests
from datetime import datetime, timezone
from twilio.rest import Client

# Stop IDs
CARROLL_F14 = "F14"
HOYT_A42 = "A42"
LAFAYETTE_F18 = "F18"

BASE_URL = "https://realtimerail.nyc/transiter/v0.6/systems/us-ny-subway/stops"

# Approximate travel time from Carroll to Lafayette (in seconds)
CARROLL_TO_LAFAYETTE_TRAVEL_TIME = 8 * 60  # ~8 minutes


def get_arrivals(stop_id):
    """Fetch arrival data for a given stop."""
    url = f"{BASE_URL}/{stop_id}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def parse_arrivals(data, direction=None, routes=None):
    """
    Parse arrivals from API response.
    Returns list of (route, arrival_time_seconds_from_now) tuples.
    """
    arrivals = []
    now = datetime.now(timezone.utc)

    stop_times = data.get("stopTimes", [])
    for st in stop_times:
        trip = st.get("trip", {})
        route_id = trip.get("route", {}).get("id", "")
        trip_direction = trip.get("direction", "")

        # Filter by direction if specified
        if direction and trip_direction != direction:
            continue

        # Filter by routes if specified
        if routes and route_id not in routes:
            continue

        arrival_info = st.get("arrival", {}) or st.get("departure", {})
        arrival_time_str = arrival_info.get("time")
        if arrival_time_str:
            arrival_time = datetime.fromisoformat(arrival_time_str.replace("Z", "+00:00"))
            seconds_until = (arrival_time - now).total_seconds()
            if seconds_until > 0:
                arrivals.append((route_id, seconds_until))

    return sorted(arrivals, key=lambda x: x[1])


def get_next_train(arrivals, routes=None):
    """Get the next train from a list of arrivals, optionally filtering by routes."""
    for route, seconds in arrivals:
        if routes is None or route in routes:
            return route, seconds
    return None, None


def check_g_switch(carroll_data, hoyt_data):
    """
    G-Switch Logic:
    If G at Carroll is 6+ min earlier than F, check A at Hoyt.
    If A arrives within 5 min of G's arrival, recommend 'G to A'.
    """
    carroll_arrivals = parse_arrivals(carroll_data, direction="NORTH")

    g_route, g_time = get_next_train(carroll_arrivals, routes=["G"])
    f_route, f_time = get_next_train(carroll_arrivals, routes=["F"])

    if g_time is None or f_time is None:
        return None, None, None

    # G must be at least 6 minutes earlier than F
    if (f_time - g_time) < 6 * 60:
        return None, None, None

    # Check A train at Hoyt
    hoyt_arrivals = parse_arrivals(hoyt_data, direction="NORTH")
    a_route, a_time = get_next_train(hoyt_arrivals, routes=["A"])

    if a_time is None:
        return None, None, None

    # Estimate arrival at Hoyt after taking G (assume 3 min transfer)
    g_to_hoyt_time = g_time + 3 * 60  # G arrival + walk time

    # A must arrive within 5 minutes of arriving at Hoyt
    if a_time <= g_to_hoyt_time + 5 * 60:
        return "G to A", g_time, a_time

    return None, None, None


def check_bd_express(carroll_data, lafayette_data):
    """
    B/D Express Logic:
    If F from Carroll arrives at Lafayette within 2 min before a Northbound B or D,
    recommend transfer.
    """
    carroll_arrivals = parse_arrivals(carroll_data, direction="NORTH")
    f_route, f_time = get_next_train(carroll_arrivals, routes=["F"])

    if f_time is None:
        return None

    # Estimated arrival at Lafayette
    f_at_lafayette = f_time + CARROLL_TO_LAFAYETTE_TRAVEL_TIME

    # Check B/D at Lafayette
    lafayette_arrivals = parse_arrivals(lafayette_data, direction="NORTH", routes=["B", "D"])

    for route, bd_time in lafayette_arrivals:
        # F arrives within 2 minutes before B/D
        if 0 <= (bd_time - f_at_lafayette) <= 2 * 60:
            return f"Transfer to {route} at Lafayette for Express"

    return None


def send_sms(message):
    """Send SMS via Twilio."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_phone = os.environ.get("TWILIO_PHONE_NUMBER")
    to_phone = os.environ.get("TO_PHONE_NUMBER")

    if not all([account_sid, auth_token, from_phone, to_phone]):
        print("Twilio credentials not configured. Message:")
        print(message)
        return

    client = Client(account_sid, auth_token)
    client.messages.create(body=message, from_=from_phone, to=to_phone)
    print(f"SMS sent: {message}")


def main():
    try:
        # Fetch data for all stops
        carroll_data = get_arrivals(CARROLL_F14)
        hoyt_data = get_arrivals(HOYT_A42)
        lafayette_data = get_arrivals(LAFAYETTE_F18)

        # Determine base recommendation
        carroll_arrivals = parse_arrivals(carroll_data, direction="NORTH")
        f_route, f_time = get_next_train(carroll_arrivals, routes=["F"])
        g_route, g_time = get_next_train(carroll_arrivals, routes=["G"])

        # Default to F
        recommended_train = "F"
        alerts = []
        transfer_advice = ""

        # Check G-Switch
        g_switch, g_arrival, a_arrival = check_g_switch(carroll_data, hoyt_data)
        if g_switch:
            recommended_train = "G"
            alerts.append(g_switch)

        # Check B/D Express
        bd_advice = check_bd_express(carroll_data, lafayette_data)
        if bd_advice:
            transfer_advice = bd_advice

        # Build message
        message_parts = [f"Take the {recommended_train}."]
        if alerts:
            message_parts.append(" ".join(alerts) + ".")
        if transfer_advice:
            message_parts.append(transfer_advice + ".")

        message = " ".join(message_parts)

        # Send SMS
        send_sms(message)

    except Exception as e:
        print(f"Error: {e}")
        send_sms(f"SubwaySentinal Error: {e}")


if __name__ == "__main__":
    main()
