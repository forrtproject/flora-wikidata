"""One credential-free, read-only API request; never creates or edits items."""
import sys
import flora_pilot as p


def plan_note():
    """Local only: say how long the saved pilot plan remains valid."""
    from datetime import datetime
    from pathlib import Path
    plan = Path(__file__).resolve().with_name("six-items.create.plan.json")
    if not plan.exists():
        return
    expires = datetime.fromisoformat(p.read_json(plan)["created_at"]).timestamp() + 86400
    when = datetime.fromtimestamp(expires).strftime("%d %b %H:%M")
    if expires > datetime.now().timestamp():
        print(f"Pilot plan valid until {when} (local time).")
    else:
        print(f"Pilot plan expired at {when}. Run REFRESH_PILOT.cmd (read-only) when the server is ready.")


def main():
    plan_note()
    print("Checking Wikidata server status. No credentials are loaded and no login or edit is attempted.")
    api = p.API("wikidata", "https://www.wikidata.org/wiki/User:Hb19988")
    # POST avoids mistaking an older cached GET response for current server health.
    # action=query remains read-only; no tokens or credentials are supplied.
    try:
        api.call({"action": "query", "meta": "siteinfo"}, post=True)
    except p.Stop as exc:
        print("NOT READY:", exc)
        print("Wait several minutes before checking again. Keep the saved plan and journal.")
        return 2
    finally:
        api.session.close()
    print("READY NOW: the server accepted a fresh read-only request with maxlag=5.")
    print("You can run CREATE_MISSING_ITEMS.cmd. Server conditions can still change before a save.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
