"""
نقطه ورود Passenger برای cPanel Python App.

تنظیمات پیشنهادی در cPanel:
  Application Root     : مسیر پروژه (خارج از public_html در صورت امکان)
  Application Startup  : passenger_wsgi.py
  Application Entry    : application
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("FLASK_ENV", "production")

from app import create_app  # noqa: E402

application = create_app(os.environ.get("FLASK_ENV", "production"))

# برخی هاست‌ها نام app را انتظار دارند
app = application
