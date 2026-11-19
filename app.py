"""
نقطه ورود برنامه.

اجرای محلی:
    python app.py
اجرا روی cPanel: فایل passenger_wsgi.py استفاده می‌شود.
"""
from __future__ import annotations

import os

from app import create_app

app = create_app(os.environ.get("FLASK_ENV", "production"))


if __name__ == "__main__":
    # فقط برای توسعه محلی؛ در هاست Passenger برنامه را اجرا می‌کند.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug)
