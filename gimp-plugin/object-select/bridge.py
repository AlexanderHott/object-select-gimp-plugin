"""A serial model process with one replaceable pending request."""

import base64
import io
import json
import queue
import subprocess
import threading

from gi.repository import GLib
from PIL import Image


def encode(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def decode(data):
    image = Image.open(io.BytesIO(base64.b64decode(data)))
    image.load()
    return image.convert("L")


class Bridge:
    def __init__(self, settings, source, on_ready, on_progress, on_error):
        self.settings = settings
        self.source = source
        self.on_ready, self.on_progress, self.on_error = on_ready, on_progress, on_error
        self.jobs = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.process = None
        self.serial = 0
        threading.Thread(target=self.run, daemon=True).start()

    def dispatch(self, callback, *args):
        def deliver():
            if not self.closed.is_set():
                callback(*args)
            return False

        GLib.idle_add(deliver)

    def submit(self, request, callback):
        self.serial += 1
        self.discard_pending()
        self.jobs.put_nowait(({**request, "id": self.serial}, callback))
        return self.serial

    def discard_pending(self):
        try:
            self.jobs.get_nowait()
        except queue.Empty:
            pass

    def exchange(self, command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        while not self.closed.is_set():
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("The model process stopped. Close and reopen Object Select.")
            response = json.loads(line)
            if "progress" in response:
                self.dispatch(self.on_progress, command["id"], response["progress"])
            else:
                return response
        return {}

    def run(self):
        try:
            command = self.settings.get("command_prefix", []) + [
                self.settings["python"],
                "-u",
                self.settings["worker"],
            ]
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
            )
            if self.closed.is_set():
                self.stop_process()
                return
            reply = self.exchange({"op": "image", "image": encode(self.source), "id": 0})
            if "error" in reply:
                raise RuntimeError(reply["error"])
            self.dispatch(self.on_ready)
            while not self.closed.is_set():
                try:
                    request, callback = self.jobs.get(timeout=0.2)
                except queue.Empty:
                    continue
                reply = self.exchange(request)
                if "masks" in reply:
                    reply["masks"] = [decode(mask) for mask in reply["masks"]]
                self.dispatch(callback, reply)
        except Exception as error:  # noqa: BLE001 - report failures at the process/UI boundary
            self.dispatch(self.on_error, str(error))
        finally:
            self.stop_process()

    def stop_process(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def close(self):
        self.closed.set()
        self.discard_pending()
        threading.Thread(target=self.stop_process, daemon=True).start()
