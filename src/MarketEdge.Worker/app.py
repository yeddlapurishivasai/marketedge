import threading

from flask import Flask, jsonify, request

from observability import configure_logging

# Route all logging through OpenTelemetry to the OS-specific file sink before any
# other module configures logging (worker.py's basicConfig then becomes a no-op).
configure_logging("marketedge-worker")

from config import Config
from db import get_connection
from rs_rating import compute_rs_ratings
from worker import get_worker_status, start_queue_listener

app = Flask(__name__)
app.config['QUEUE_NAME'] = Config.QUEUE_NAME


@app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "MarketEdge.Worker"})


@app.route('/status')
def status():
    return jsonify(get_worker_status())


@app.route('/steps/compute-rs', methods=['POST'])
def compute_rs():
    payload = request.get_json(silent=True) or {}
    market = str(payload.get("market", "")).lower()
    if market not in ("india", "us"):
        return jsonify({"error": "market must be 'india' or 'us'"}), 400
    test_sample_only = bool(payload.get("testSampleOnly"))
    conn = None
    try:
        conn = get_connection()
        summary = compute_rs_ratings(conn, market, test_sample_only=test_sample_only)
        return jsonify(summary)
    except Exception as exc:  # pragma: no cover - surfaced to caller
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


listener_thread = threading.Thread(target=start_queue_listener, daemon=True, name='queue-listener')
listener_thread.start()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
