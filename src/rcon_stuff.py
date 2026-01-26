from mcrcon import MCRcon
import os
import re


def get_player_info(alderon_id):
    print(alderon_id)
    try:
        with MCRcon(os.getenv('RCON_IP'), os.getenv('RCON_PASS'), port=int(os.getenv('RCON_PORT')), timeout=5) as rcon:
            response = rcon.command(f"/playerinfo {alderon_id}")
    except Exception as e:
        print(f"Rcon Error: {e}")
        return None

    response_clean = re.sub(r"^\(playerinfo [^)]+\):\s*", "", response)
    print(f"Cleaned response -> {response_clean!r}")
    fields: dict[str, str] = {}
    for segment in response_clean.split(" / "):
        if ":" not in segment:
            continue
        key, value = map(str.strip, segment.split(":", 1))
        fields[key.lower()] = value.strip()

    return fields
