import argparse
import os
import requests
from datetime import datetime, timezone

# Stop IDs
CARROLL_F21 = "F21"  # Carroll St
HOYT_A42 = "A42"     # Hoyt-Schermerhorn Sts
LAFAYETTE_D21 = "D21"  # Broadway-Lafayette St

BASE_URL = "https://realtimerail.nyc/transiter/v0.6/systems/us-ny-subway/stops"

# Approximate travel time from Carroll to Lafayette (in seconds)
CARROLL_TO_LAFAYETTE_TRAVEL_TIME = 8 * 60  # ~8 minutes


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


def check_bd_express(carroll_data, lafayette_data):
    """
    B/D Express Logic:
    If F from Carroll arrives at Lafayette within 2 min before a Northbound B or D,
    recommend transfer.
    """
    carroll_arrivals = parse_arrivals(carroll_data)
    f_route, f_time = get_next_train(carroll_arrivals, routes=["F"])

    if f_time is None:
        return None

    # Estimated arrival at Lafayette
    f_at_lafayette = f_time + CARROLL_TO_LAFAYETTE_TRAVEL_TIME

    # Check B/D at Lafayette
    lafayette_arrivals = parse_arrivals(lafayette_data, routes=["B", "D"])

    for route, bd_time in lafayette_arrivals:
        # F arrives within 2 minutes before B/D
        if 0 <= (bd_time - f_at_lafayette) <= 3 * 60:
            return f"Transfer to {route} at Lafayette for Express"

    return None


def send_telegram(message):
    """Send message via Telegram bot."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not all([bot_token, chat_id]):
        print("Telegram credentials not configured. Message:")
        print(message)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    response = requests.post(url, json=payload, timeout=10)
    
    if response.ok:
        print(f"Telegram sent: {message}")
    else:
        print(f"Telegram error: {response.text}")


def main(dry_run=False):
    try:
        # Fetch data for all stops
        carroll_data = get_arrivals(CARROLL_F21)
        hoyt_data = get_arrivals(HOYT_A42)
        lafayette_data = get_arrivals(LAFAYETTE_D21)

        # Determine base recommendation
        carroll_arrivals = parse_arrivals(carroll_data)
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

        # Debug info for dry run
        if dry_run:
            print("=== DRY RUN ===")
            print(f"F train: {f_time/60:.1f} min" if f_time else "F train: Not found")
            print(f"G train: {g_time/60:.1f} min" if g_time else "G train: Not found")
            print(f"G-Switch: {g_switch or 'No'}")
            print()
            
            # B/D Express diagnostics
            print("=== B/D Express Diagnostics ===")
            if f_time:
                f_at_lafayette = f_time + CARROLL_TO_LAFAYETTE_TRAVEL_TIME
                print(f"F arrives at Lafayette in: {f_at_lafayette/60:.1f} min")
                
                lafayette_arrivals = parse_arrivals(lafayette_data, routes=["B", "D"])
                if lafayette_arrivals:
                    # Find first two catchable trains (wait >= 0)
                    catchable = []
                    for route, bd_time in lafayette_arrivals:
                        wait_at_lafayette = bd_time - f_at_lafayette
                        if wait_at_lafayette >= 0:
                            viable = "✓ TRANSFER" if wait_at_lafayette <= 3 * 60 else ""
                            catchable.append((route, bd_time, wait_at_lafayette, viable))
                            if len(catchable) >= 2:
                                break
                    
                    if catchable:
                        print(f"  First catchable trains:")
                        for route, bd_time, wait, viable in catchable:
                            print(f"    {route} in {bd_time/60:.1f} min (wait: {wait/60:.1f} min) {viable}")
                    else:
                        print("  No catchable B/D trains found")
                else:
                    print("  No B/D trains found at Lafayette")
            else:
                print("  Cannot calculate - no F train found")
            
            print(f"\nB/D Express: {bd_advice or 'No'}")
            print(f"\nMessage: {message}")
        else:
            send_telegram(message)

    except Exception as e:
        print(f"Error: {e}")
        if not dry_run:
            send_telegram(f"SubwaySentinal Error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subway route advisor")
    parser.add_argument("--dry-run", action="store_true", help="Print output without sending SMS")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
