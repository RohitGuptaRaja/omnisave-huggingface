import sys
from datetime import datetime

class UltimateLogger:
    def _write(self, msg):
        try:
            with open("omni_logs.txt", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except:
            pass

    def info(self, msg):
        time_now = datetime.now().strftime('%H:%M:%S')
        full_msg = f"{time_now} | [INFO] | {msg}"
        print(full_msg, flush=True)
        self._write(full_msg)

    def error(self, msg):
        time_now = datetime.now().strftime('%H:%M:%S')
        full_msg = f"{time_now} | [ERROR] | {msg}"
        print(full_msg, file=sys.stderr, flush=True)
        self._write(full_msg)

    def warning(self, msg):
        time_now = datetime.now().strftime('%H:%M:%S')
        full_msg = f"{time_now} | [WARNING] | {msg}"
        print(full_msg, flush=True)
        self._write(full_msg)

logger = UltimateLogger()

# Purane logs clear karne ke liye
with open("omni_logs.txt", "w", encoding="utf-8") as f:
    f.write("🔥 GOD MODE LOGS STARTED 🔥\n==============================\n")
