import requests
from fastapi import FastAPI

app = FastAPI()


def fetch(url: str):
    return requests.get(url, timeout=5)
