# 샘플 Python 파일 - 다양한 import 형태 테스트용
import os
import sys
import requests
import numpy as np
from fastapi import FastAPI, UploadFile
import cv2                      # alias 보정 테스트: cv2 -> opencv-python
from bs4 import BeautifulSoup   # alias 보정 테스트: bs4 -> beautifulsoup4
import pandas as pd

app = FastAPI()


def fetch_data(url: str):
    response = requests.get(url)
    return response.json()


def process(data):
    arr = np.array(data)
    return arr.mean()
