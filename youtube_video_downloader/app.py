import os
import re
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO
import yt_dlp
import threading
import zipfile

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

progress_data = {"percent": 0, "status": "idle", "filename": None}


def safe_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)


def progress_hook(d):
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes", 0)

        if total:
            percent = int(downloaded * 100 / total)

            progress_data.update({
                "percent": percent,
                "status": "downloading",
                "speed": d.get("speed") and f"{round(d['speed']/1024/1024,2)} MB/s",
                "eta": d.get("eta")
            })

            socketio.emit("progress", progress_data)

    elif d["status"] == "finished":
        progress_data.update({"percent": 100, "status": "processing"})
        socketio.emit("progress", progress_data)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    quality = request.form.get("quality")

    if not url:
        return "URL required", 400

    progress_data.update({"percent": 0, "status": "starting", "filename": None})
    socketio.emit("progress", progress_data)

    if quality == "720":
        fmt = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    elif quality == "480":
        fmt = "bestvideo[height<=480]+bestaudio/best[height<=480]"
    elif quality == "audio":
        fmt = "bestaudio"
    else:
        fmt = "best"

    def run_download():
        try:
            ydl_opts = {
                "format": fmt,
                "outtmpl": os.path.join(DOWNLOAD_FOLDER, "%(title)s.%(ext)s"),
                "noplaylist": True,
                "progress_hooks": [progress_hook],
            }

            # MP3 conversion
            if quality == "audio":
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                filename = ydl.prepare_filename(info)
                filename = os.path.splitext(filename)[0] + (".mp3" if quality == "audio" else os.path.splitext(filename)[1])

                progress_data.update({
                    "filename": filename,
                    "status": "completed",
                    "thumbnail": info.get("thumbnail")
                })

                socketio.emit("progress", progress_data)

        except Exception as e:
            progress_data.update({"status": f"error: {str(e)}"})
            socketio.emit("progress", progress_data)

    socketio.start_background_task(run_download)
    return jsonify({"message": "started"})


@app.route("/file")
def get_file():
    filename = progress_data.get("filename")

    if filename and os.path.exists(filename):
        return send_file(filename, as_attachment=True)

    return "File not ready", 404


@app.route("/zip")
def download_zip():
    zip_path = os.path.join(DOWNLOAD_FOLDER, "all_downloads.zip")

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for file in os.listdir(DOWNLOAD_FOLDER):
            fp = os.path.join(DOWNLOAD_FOLDER, file)
            if os.path.isfile(fp) and not file.endswith(".zip"):
                zipf.write(fp, file)

    return send_file(zip_path, as_attachment=True)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
