"""Phone notifications through ntfy (https://ntfy.sh). Set NTFY_TOPIC in .env to turn them on; blank means off.
Sending never raises: a missing network must not break the bot or an update.
From a shell script:  python -m population.notify "Title" "Message" [high]"""
import os
import sys
import urllib.request

from .quiet import read_env


def settings(app_dir):
    "(server, topic, name) from the environment, falling back to the .env file."
    env = read_env(os.path.join(app_dir, ".env"))
    get = lambda key, default="": (os.environ.get(key) or env.get(key) or default).strip()
    return get("NTFY_SERVER", "https://ntfy.sh").rstrip("/"), get("NTFY_TOPIC"), get("NTFY_NAME", "Crownfall")


def send(app_dir, title, message, priority="default"):
    "Returns True if ntfy accepted it. Does nothing (False) when no topic is set."
    server, topic, name = settings(app_dir)
    if not topic:
        return False
    req = urllib.request.Request("%s/%s" % (server, topic), data=message.encode(), method="POST", headers={
        "Title": ("%s: %s" % (name, title)).encode("ascii", "replace").decode(), "Priority": priority})
    try:
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    args = sys.argv[1:]
    send(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args[0], args[1] if len(args) > 1 else "",
         args[2] if len(args) > 2 else "default")
