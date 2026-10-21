"""Run the local UniMatch PH prototype (no university requests on startup)."""
import argparse

from webapp import create_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    create_app().run(host=args.host, port=args.port, debug=False)
