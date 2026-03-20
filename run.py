import os
import sys
import json
import time
import signal
import shutil
import threading
from pathlib import Path
from datetime import datetime

import requests
import mutagen
from rich.console import Console
from rich.table import Table
from rich.live import Live

interrupt_requested = threading.Event()
current_prompt_id: list[str | None] = [None]


def interrupt_task():
    pid = current_prompt_id[0]
    if pid:
        try:
            requests.post(
                f"{COMFYUI_URL}/interrupt", json={"prompt_id": pid}, timeout=5
            )
        except Exception:
            pass


COMFYUI_URL = "http://100.91.232.13:8188"

console = Console()


def scan_folder(folder_path: str):
    folder = Path(folder_path)
    supported_image = (".png", ".jpg", ".jpeg", ".webp")
    supported_audio = (".wav", ".mp3", ".flac", ".ogg", ".m4a")

    images = [f for f in folder.iterdir() if f.suffix.lower() in supported_image]
    audios = [f for f in folder.iterdir() if f.suffix.lower() in supported_audio]

    if not images:
        raise ValueError("No image found in folder")
    if not audios:
        raise ValueError("No audio found in folder")

    image = images[0]
    audios = sorted(audios)

    return image, audios


def get_image_orientation(image_path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(image_path) as img:
        w, h = img.size
    if w > h:
        return 1280, 720
    else:
        return 720, 1280


def get_audio_duration(audio_path: str) -> float:
    audio = mutagen.File(audio_path)
    if audio is None:
        raise ValueError(f"Cannot read audio: {audio_path}")
    return audio.info.length


def upload_file(file_path: str, subfolder: str = "") -> str:
    filename = os.path.basename(file_path)
    mime = (
        "image/png"
        if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        else "audio/wav"
    )
    with open(file_path, "rb") as f:
        response = requests.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (filename, f, mime)},
            data={"subfolder": subfolder} if subfolder else {},
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Upload failed ({response.status_code}): {response.text[:500]}"
        )
    return response.json()["name"]


def upload_audio(file_path: str, subfolder: str = "") -> str:
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        response = requests.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (filename, f, "audio/wav")},
            data={"subfolder": subfolder} if subfolder else {},
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Audio upload failed ({response.status_code}): {response.text[:500]}"
        )
    return response.json()["name"]


def submit_task(workflow: dict) -> str:
    response = requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": workflow},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to submit task ({response.status_code}): {response.text[:1000]}"
        )
    return response.json()["prompt_id"]


def get_task_status(prompt_id: str) -> dict | None:
    response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
    if response.status_code != 200:
        return None
    history = response.json()
    return history.get(prompt_id)


class Task:
    def __init__(
        self, index: int, audio_path: Path, width: int, height: int, duration: float
    ):
        self.index = index
        self.audio_name = audio_path.name
        self.audio_path = audio_path
        self.width = width
        self.height = height
        self.duration = duration
        self.status = "pending"
        self.prompt_id: str | None = None
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.error: str | None = None

    @property
    def elapsed(self) -> float | None:
        if self.start_time is None:
            return None
        end = self.end_time or time.time()
        return end - self.start_time

    def elapsed_str(self) -> str:
        e = self.elapsed
        if e is None:
            return "-"
        m, s = divmod(int(e), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


def build_workflow(
    image_path: str,
    audio_path: str,
    width: int,
    height: int,
    duration: float,
    workflow_template: dict,
) -> dict:
    wf = json.loads(json.dumps(workflow_template))

    wf["327"]["inputs"]["image"] = os.path.basename(image_path)
    wf["321"]["inputs"]["audio"] = os.path.basename(audio_path)
    wf["321"]["inputs"]["duration"] = duration
    wf["322"]["inputs"]["value"] = width
    wf["323"]["inputs"]["value"] = height

    return wf


def render_table(tasks: list[Task]):
    table = Table(title="ComfyUI Batch Video Generator")
    table.add_column("#", style="cyan", width=4)
    table.add_column("Audio", style="white", width=30)
    table.add_column("Status", width=12)
    table.add_column("Elapsed", justify="right", width=10)
    table.add_column("Error", style="red", width=40)

    for task in tasks:
        status_color = {
            "pending": "yellow",
            "running": "cyan",
            "done": "green",
            "error": "red",
        }.get(task.status, "white")

        status_text = {
            "pending": "Pending",
            "running": "Running...",
            "done": "Done",
            "error": "Error",
        }.get(task.status, task.status)

        error_msg = str(task.error) if task.status == "error" and task.error else ""

        table.add_row(
            str(task.index),
            task.audio_name,
            f"[{status_color}]{status_text}[/{status_color}]",
            task.elapsed_str(),
            error_msg,
        )

    return table


def main():
    def handle_signal(signum, frame):
        interrupt_requested.set()
        interrupt_task()
        console.print("\n[yellow]Interrupted. Cancelling current task...[/yellow]")
        sys.exit(130)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if len(sys.argv) < 2:
        console.print("[red]Usage: python run.py <folder_path> [workflow.json][/red]")
        sys.exit(1)

    folder = sys.argv[1]
    workflow_path = sys.argv[2] if len(sys.argv) > 2 else "workflow.json"

    console.print(f"[cyan]Scanning folder:[/cyan] {folder}")
    image, audios = scan_folder(folder)
    console.print(f"[green]Found image:[/green] {image.name}")
    console.print(f"[green]Found {len(audios)} audio(s)[/green]")

    width, height = get_image_orientation(str(image))
    console.print(f"[green]Resolution:[/green] {width} x {height}")

    tasks: list[Task] = []
    for i, audio in enumerate(audios):
        duration = get_audio_duration(str(audio))
        tasks.append(Task(i + 1, audio, width, height, duration))

    console.print(f"[cyan]Loading workflow:[/cyan] {workflow_path}")
    with open(workflow_path) as f:
        workflow_template = json.load(f)

    completed_count = 0

    console.print("[cyan]Uploading image...[/cyan]")
    uploaded_image = upload_file(str(image))
    console.print(f"[green]Image uploaded:[/green] {uploaded_image}")

    with Live(render_table(tasks), console=console, refresh_per_second=4) as live:
        for task in tasks:
            task.status = "running"
            task.start_time = time.time()

            try:
                console.print(f"[cyan]Uploading audio:[/cyan] {task.audio_name}")
                uploaded_audio = upload_audio(str(task.audio_path))
                console.print(f"[green]Audio uploaded:[/green] {uploaded_audio}")

                wf = build_workflow(
                    uploaded_image,
                    uploaded_audio,
                    task.width,
                    task.height,
                    task.duration,
                    workflow_template,
                )
                prompt_id = submit_task(wf)
                task.prompt_id = prompt_id
                current_prompt_id[0] = prompt_id
            except Exception as e:
                task.status = "error"
                task.error = str(e)
                task.end_time = time.time()
                console.print(f"[red]Task {task.index} error:[/red]")
                console.print(f"[red]{e}[/red]")
                live.update(render_table(tasks))
                completed_count += 1
                continue

            while True:
                if interrupt_requested.is_set():
                    task.status = "error"
                    task.error = "Cancelled by user"
                    task.end_time = time.time()
                    completed_count += 1
                    break
                status = get_task_status(prompt_id)
                if status is not None:
                    status_str = status.get("status", {}).get("str", "success")
                    errors = status.get("status", {}).get("messages", [])
                    error_msgs = [m[1] for m in errors if m[0] == "execution_error"]
                    if status_str != "success" or error_msgs:
                        task.status = "error"
                        if error_msgs:
                            err = error_msgs[0]
                            if isinstance(err, dict):
                                task.error = f"{err.get('exception_type', 'Error')}: {err.get('exception_message', str(err))}"
                            else:
                                task.error = str(err)
                        else:
                            task.error = f"ComfyUI status: {status_str}"
                        task.end_time = time.time()
                        completed_count += 1
                    else:
                        task.status = "done"
                        task.end_time = time.time()
                        completed_count += 1
                    current_prompt_id[0] = None
                    break
                if task.elapsed and task.elapsed > 3600:
                    task.status = "error"
                    task.error = "Timeout (>1h)"
                    task.end_time = time.time()
                    completed_count += 1
                    current_prompt_id[0] = None
                    break
                time.sleep(2)
                live.update(render_table(tasks))

            live.update(render_table(tasks))

    console.print(
        f"\n[green]All done! {completed_count}/{len(tasks)} completed.[/green]"
    )


if __name__ == "__main__":
    main()
