

import platform
import re
import statistics
import subprocess
import time


TARGET = "google.com"   
COUNT = 10              
DELAY = 1               


is_windows = platform.system().lower() == "windows"
latencies = []
lost = 0

print(f"Testing {TARGET} ({COUNT} pings)...\n")

for i in range(COUNT):
    # Traffic generation: send ONE ping
    if is_windows:
        cmd = ["ping", "-n", "1", "-w", "1000", TARGET]
    else:
        cmd = ["ping", "-c", "1", TARGET]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout
    except subprocess.TimeoutExpired:
        output = ""

    # Performance measurement:
    match = re.search(r"time[=<]\s*([\d.]+)\s*ms", output)
    if match:
        ms = float(match.group(1))
        latencies.append(ms)
        print(f"Ping {i + 1}: {ms} ms")
    else:
        lost += 1
        print(f"Ping {i + 1}: no reply")

    time.sleep(DELAY)

# ---- Results ----
print("\n--- Results ---")
print(f"Packets sent    : {COUNT}")
print(f"Packets received: {len(latencies)}")
print(f"Packet loss     : {lost / COUNT * 100:.1f}%")

if latencies:
    print(f"Min latency     : {min(latencies):.1f} ms")
    print(f"Average latency : {sum(latencies) / len(latencies):.1f} ms")
    print(f"Max latency     : {max(latencies):.1f} ms")

    # Jitter = average difference between consecutive latency values
    if len(latencies) > 1:
        diffs = [abs(latencies[k] - latencies[k - 1]) for k in range(1, len(latencies))]
        print(f"Jitter          : {statistics.mean(diffs):.1f} ms")
else:
    print("No replies received - check your connection or the target.")
