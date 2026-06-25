import threading

from flask import Flask, jsonify

from observability import configure_logging

# Route all logging through OpenTelemetry to the OS-specific file sink before any
# other module configures logging (worker.py's basicConfig then becomes a no-op).
configure_logging("marketedge-worker")

from config import Config
from worker import get_worker_status, start_queue_listener

app = Flask(__name__)
app.config['QUEUE_NAME'] = Config.QUEUE_NAME


@app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "MarketEdge.Worker"})


@app.route('/status')
def status():
    return jsonify(get_worker_status())


listener_thread = threading.Thread(target=start_queue_listener, daemon=True, name='queue-listener')
listener_thread.start()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
