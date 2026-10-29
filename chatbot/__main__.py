"""python -m chatbot [--port 8000] [--host 127.0.0.1]"""
import argparse

import uvicorn

parser = argparse.ArgumentParser(description="Run the website chatbot server.")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()
uvicorn.run("chatbot.main:app", host=args.host, port=args.port)
