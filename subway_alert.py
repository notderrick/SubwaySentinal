import argparse
import os
import requests
from datetime import datetime, timezone

# Stop IDs - Manhattan-bound direction (N suffix)
# From Carroll/Smith-9th, "N" goes toward Queens via Manhattan (34th St)
# From Lafayette, "N" goes toward Bronx via Manhattan
CARROLL_STOP = "F21N"      # Carroll St (Manhattan-bound)
SMITH_9TH_STOP = "F22N"    # Smith-9th Sts (Manhattan-bound)
HOYT_STOP = "A42N"         # Hoyt-Schermerhorn Sts (A train for G-Switch)
LAFAYETTE_STOP = "D21N"    # Broadway-Lafayette St (B/D Manhattan-bound)

BASE_URL = "https://realtimerail.nyc/transiter/v0.6/systems/us-ny-subway/stops"
ROUTE_BASE_URL = "https://realtimerail.nyc/transiter/v0.6/systems/us-ny-subway/routes"

# Travel times will be calculated dynamically, but keep defaults as fallback
CARROLL_TO_LAFAYETTE_TRAVEL_TIME = 8 * 60  # ~8 minutes
SMITH_9TH_TO_LAFAYETTE_TRAVEL_TIME = 6 * 60  # ~6 minutes


def get_arrivals(stop_id):
    """Fetch arrival data for a given stop."""
    url = f"{BASE_URL}/{stop_id}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def parse_arrivals(data, routes=None):
    """
    Parse arrivals from API response.
    Returns list of (route, arrival_time_seconds_from_now) tuples.
    """
    arrivals = []
    now = datetime.now(timezone.utc).timestamp()

    stop_times = data.get("stopTimes", [])
    for st in stop_times:
        trip = st.get("trip", {})
        route_id = trip.get("route", {}).get("id", "")

        # Filter by routes if specified
        if routes and route_id not in routes:
            continue

        arrival_info = st.get("arrival", {}) or st.get("departure", {})
        arrival_time_str = arrival_info.get("time")
        if arrival_time_str:
            # API returns Unix timestamp as string
            arrival_time = int(arrival_time_str)
            seconds_until = arrival_time - now
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
    carroll_arrivals = parse_arrivals(carroll_data)

    g_route, g_time = get_next_train(carroll_arrivals, routes=["G"])
    f_route, f_time = get_next_train(carroll_arrivals, routes=["F"])

    if g_time is None or f_time is None:
        return None, None, None

    # G must be at least 6 minutes earlier than F
    if (f_time - g_time) < 6 * 60:
        return None, None, None

    # Check A train at Hoyt
    hoyt_arrivals = parse_arrivals(hoyt_data)
    a_route, a_time = get_next_train(hoyt_arrivals, routes=["A"])

    if a_time is None:
        return None, None, None

    # Estimate arrival at Hoyt after taking G (assume 3 min transfer)
    g_to_hoyt_time = g_time + 3 * 60  # G arrival + walk time

    # A must arrive within 5 minutes of arriving at Hoyt
    if a_time <= g_to_hoyt_time + 5 * 60:
        return "G to A", g_time, a_time

    return None, None, None


def check_bd_express(station_data, lafayette_data, travel_time=CARROLL_TO_LAFAYETTE_TRAVEL_TIME):
    """
    B/D Express Logic:
    If F from station arrives at Lafayette within 2 min before a Northbound B or D,
    recommend transfer.
    """
    station_arrivals = parse_arrivals(station_data)
    f_route, f_time = get_next_train(station_arrivals, routes=["F"])

    if f_time is None:
        return None

    # Estimated arrival at Lafayette
    f_at_lafayette = f_time + travel_time

    # Check B/D at Lafayette
    lafayette_arrivals = parse_arrivals(lafayette_data, routes=["B", "D"])

    for route, bd_time in lafayette_arrivals:
        # F arrives within 3 minutes before B/D
        if 0 <= (bd_time - f_at_lafayette) <= 3 * 60:
            return f"Transfer to {route} at Lafayette for Express"


def get_service_alerts(routes):
    """Fetch active service alerts for specified routes.
    
    Two-stage approach:
    1. Get alert IDs from each route endpoint
    2. Fetch full alert details for each unique ID
    """
    alert_ids = set()
    
    # Stage 1: Collect alert IDs from each route
    for route in routes:
        try:
            url = f"{ROUTE_BASE_URL}/{route}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            route_alerts = data.get("alerts", [])
            for alert in route_alerts:
                alert_id = alert.get("id")
                if alert_id:
                    alert_ids.add((alert_id, route))
        except Exception as e:
            print(f"Error fetching route {route}: {e}")
            continue
    
    # Stage 2: Fetch full details for each alert
    alerts = []
    seen_ids = set()
    
    for alert_id, route in alert_ids:
        if alert_id in seen_ids:
            continue
        seen_ids.add(alert_id)
        
        try:
            url = f"https://realtimerail.nyc/transiter/v0.6/systems/us-ny-subway/alerts/{alert_id}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            alert = response.json()
            
            header_list = alert.get("header", [])
            header = "Service Alert"
            if header_list and isinstance(header_list, list) and len(header_list) > 0 and "text" in header_list[0]:
                header = header_list[0]["text"]
                
            desc_list = alert.get("description", [])
            description = ""
            if desc_list and isinstance(desc_list, list) and len(desc_list) > 0 and "text" in desc_list[0]:
                description = desc_list[0]["text"]

            alerts.append({
                "route": route,
                "header": header,
                "description": description
            })
        except Exception as e:
            print(f"Error fetching alert {alert_id}: {e}")
            continue
            
    return alerts


def send_email(message):
    """Send email alert via SMTP."""
    import smtplib
    from email.mime.text import MIMEText
    
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("TO_EMAIL")

    if not all([smtp_user, smtp_password, to_email]):
        print("Email credentials not configured. Message:")
        print(message)
        return

    msg = MIMEText(message)
    msg["Subject"] = "🚇 SubwaySentinal Alert"
    msg["From"] = smtp_user
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())
        print(f"Email sent: {message}")
    except Exception as e:
        print(f"Email error: {e}")


def get_station_report(station_name, station_stop, hoyt_data, lafayette_data, travel_time):
    """Generate report for a single station."""
    lines = []
    
    station_data = get_arrivals(station_stop)
    station_arrivals = parse_arrivals(station_data)
    f_route, f_time = get_next_train(station_arrivals, routes=["F"])
    g_route, g_time = get_next_train(station_arrivals, routes=["G"])

    # Default recommendation
    recommended_train = "F"

    # Check G-Switch
    g_switch, g_arrival, a_arrival = check_g_switch(station_data, hoyt_data)
    if g_switch:
        recommended_train = "G"

    # Check B/D Express
    bd_advice = check_bd_express(station_data, lafayette_data, travel_time)
    if bd_advice:
        if "B" in bd_advice:
            recommended_train = "F → B"
        elif "D" in bd_advice:
            recommended_train = "F → D"

    lines.append(f"📍 FROM {station_name.upper()}")
    lines.append(f"🚇 Recommendation: {recommended_train}")
    lines.append(f"  F train: {f_time/60:.1f} min" if f_time else "  F train: Not found")
    lines.append(f"  G train: {g_time/60:.1f} min" if g_time else "  G train: Not running")
    
    if g_switch:
        lines.append(f"  ⚠️ G-SWITCH: {g_switch}")
    
    if f_time:
        f_at_lafayette = f_time + travel_time
        lines.append(f"  → Lafayette in: {f_at_lafayette/60:.1f} min")
        
        lafayette_arrivals = parse_arrivals(lafayette_data, routes=["B", "D"])
        if lafayette_arrivals:
            for route, bd_time in lafayette_arrivals[:2]:
                wait = bd_time - f_at_lafayette
                if wait >= 0:
                    viable = "✓" if wait <= 3 * 60 else ""
                    lines.append(f"    {route} in {bd_time/60:.1f} min (wait: {wait/60:.1f} min) {viable}")
    
    return lines


def main(dry_run=False):
    try:
        # Fetch shared data
        hoyt_data = get_arrivals(HOYT_STOP)
        lafayette_data = get_arrivals(LAFAYETTE_STOP)

        # Build email with both stations
        lines = []
        
        # Carroll St
        lines.extend(get_station_report(
            "Carroll St", CARROLL_STOP, hoyt_data, lafayette_data, 
            CARROLL_TO_LAFAYETTE_TRAVEL_TIME
        ))
        
        lines.append("")
        lines.append("─" * 30)
        lines.append("")
        
        # Smith-9th
        lines.extend(get_station_report(
            "Smith-9th Sts", SMITH_9TH_STOP, hoyt_data, lafayette_data,
            SMITH_9TH_TO_LAFAYETTE_TRAVEL_TIME
        ))

        # Service Alerts (shared)
        service_alerts = get_service_alerts(["F", "G", "B", "D"])
        if service_alerts:
            lines.append("")
            lines.append("⚠️ SERVICE ALERTS")
            for alert in service_alerts:
                lines.append(f"[{alert['route']}] {alert['header']}")

        message = "\n".join(lines)

        if dry_run:
            print("=== DRY RUN ===")
            print(message)
        else:
            send_email(message)

    except Exception as e:
        print(f"Error: {e}")
        if not dry_run:
            send_email(f"SubwaySentinal Error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subway route advisor")
    parser.add_argument("--dry-run", action="store_true", help="Print output without sending SMS")
    args = parser.parse_args()
    main(dry_run=args.dry_run)

