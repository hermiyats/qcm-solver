"""QCM Solver — cross-platform desktop client.

A small tkinter app whose job is to let the user snip parts of their screen,
collect them, and POST them to the middle-layer API, then show the answer.

Flow:
  1. Main window shows a single "New" button (plus controls that appear once
     screenshots exist).
  2. "New" hides the window and lets the user drag-select a screen region.
     The captured region is kept in memory (a PIL Image).
  3. A thumbnail carousel shows every captured screenshot. The user can add
     more (+) or remove one.
  4. "Send" uploads every screenshot to the API endpoint and displays the
     returned answer.

OS independence: screen capture uses `mss` (Windows/macOS/Linux), image work
uses Pillow, and the HTTP call uses `requests`. No platform-specific code.
"""

import base64
import io
import os
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import mss
import requests
from PIL import Image, ImageTk

# Where the middle layer lives. Override with the QCM_ENDPOINT env var, e.g.
#   QCM_ENDPOINT=https://your-project.vercel.app/api/solve
DEFAULT_ENDPOINT = os.environ.get("QCM_ENDPOINT", "https://qcm-solver.vercel.app/api/solve")

THUMB_SIZE = (140, 100)
REQUEST_TIMEOUT = 180  # seconds — Claude may think for a while on hard questions


class RegionSelector:
    """Fullscreen overlay that lets the user drag a rectangle over a frozen
    screenshot of their screen and returns the cropped PIL Image."""

    def __init__(self, root):
        self.root = root
        self.result = None

        # Grab the whole primary screen first, then show it fullscreen. Selecting
        # on the captured image (rather than a transparent overlay) is the most
        # reliable approach across platforms and avoids compositor quirks.
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # [0] is the union of all monitors; [1] is primary
            shot = sct.grab(monitor)
            self.screenshot = Image.frombytes("RGB", shot.size, shot.rgb)

        self.top = tk.Toplevel(root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.configure(cursor="cross")

        self.canvas = tk.Canvas(self.top, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.tk_image = ImageTk.PhotoImage(self.screenshot)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        # Dim hint text.
        self.canvas.create_text(
            self.screenshot.width // 2,
            30,
            text="Drag to select a region  •  Esc to cancel",
            fill="#ffec3d",
            font=("TkDefaultFont", 14, "bold"),
        )

        self.start_x = self.start_y = 0
        self.rect_id = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", lambda _e: self._cancel())

        # Block until the overlay is dismissed.
        self.top.grab_set()
        self.root.wait_window(self.top)

    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#ff3b30", width=2,
        )

    def _on_drag(self, event):
        if self.rect_id is not None:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def _on_release(self, event):
        x1, x2 = sorted((self.start_x, event.x))
        y1, y2 = sorted((self.start_y, event.y))

        # Map canvas coordinates to image pixels (handles any HiDPI scaling
        # between the fullscreen canvas and the captured image).
        cw = self.canvas.winfo_width() or self.screenshot.width
        ch = self.canvas.winfo_height() or self.screenshot.height
        sx = self.screenshot.width / cw
        sy = self.screenshot.height / ch
        box = (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))

        if box[2] - box[0] < 5 or box[3] - box[1] < 5:
            # Too small to be a deliberate selection — treat as a cancel.
            self._cancel()
            return

        self.result = self.screenshot.crop(box)
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()


class QCMApp:
    def __init__(self, root):
        self.root = root
        self.root.title("QCM Solver")
        self.root.geometry("640x560")
        self.root.minsize(560, 480)

        self.screenshots = []          # list[PIL.Image]
        self._thumb_refs = []          # keep ImageTk refs alive

        self._build_ui()
        self._refresh()

    # ---------- UI construction ----------

    def _build_ui(self):
        # Top bar: endpoint field.
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Endpoint:").pack(side="left")
        self.endpoint_var = tk.StringVar(value=DEFAULT_ENDPOINT)
        ttk.Entry(top, textvariable=self.endpoint_var).pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Action buttons.
        actions = ttk.Frame(self.root, padding=(10, 4))
        actions.pack(fill="x")
        self.new_btn = ttk.Button(actions, text="New", command=self.on_new)
        self.new_btn.pack(side="left")
        self.add_btn = ttk.Button(actions, text="+ Add screenshot", command=self.on_new)
        self.add_btn.pack(side="left", padx=(6, 0))
        self.send_btn = ttk.Button(actions, text="Send", command=self.on_send)
        self.send_btn.pack(side="left", padx=(6, 0))
        self.clear_btn = ttk.Button(actions, text="Clear all", command=self.on_clear)
        self.clear_btn.pack(side="left", padx=(6, 0))

        # Carousel (horizontally scrollable thumbnails).
        carousel_wrap = ttk.LabelFrame(self.root, text="Screenshots", padding=6)
        carousel_wrap.pack(fill="x", padx=10, pady=(6, 4))

        self.carousel_canvas = tk.Canvas(carousel_wrap, height=THUMB_SIZE[1] + 36, highlightthickness=0)
        hbar = ttk.Scrollbar(carousel_wrap, orient="horizontal", command=self.carousel_canvas.xview)
        self.carousel_canvas.configure(xscrollcommand=hbar.set)
        self.carousel_canvas.pack(fill="x", side="top")
        hbar.pack(fill="x", side="bottom")

        self.carousel_inner = ttk.Frame(self.carousel_canvas)
        self.carousel_canvas.create_window((0, 0), window=self.carousel_inner, anchor="nw")
        self.carousel_inner.bind(
            "<Configure>",
            lambda _e: self.carousel_canvas.configure(scrollregion=self.carousel_canvas.bbox("all")),
        )

        # Answer area.
        answer_wrap = ttk.LabelFrame(self.root, text="Answer", padding=6)
        answer_wrap.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.answer_text = scrolledtext.ScrolledText(answer_wrap, wrap="word", height=8, state="disabled")
        self.answer_text.pack(fill="both", expand=True)

        # Status bar.
        self.status_var = tk.StringVar(value="Click “New” to capture a screenshot.")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    # ---------- actions ----------

    def on_new(self):
        # Hide the main window so it isn't part of the screenshot, give the
        # compositor a moment, then show the selector.
        self.root.withdraw()
        self.root.after(200, self._capture)

    def _capture(self):
        try:
            selector = RegionSelector(self.root)
        finally:
            self.root.deiconify()
            self.root.lift()

        if selector.result is not None:
            self.screenshots.append(selector.result)
            self.status_var.set(f"Captured screenshot {len(self.screenshots)}.")
        else:
            self.status_var.set("Capture cancelled.")
        self._refresh()

    def on_clear(self):
        self.screenshots.clear()
        self._set_answer("")
        self.status_var.set("Cleared. Click “New” to start again.")
        self._refresh()

    def _remove(self, index):
        del self.screenshots[index]
        self.status_var.set("Removed a screenshot.")
        self._refresh()

    def on_send(self):
        if not self.screenshots:
            messagebox.showinfo("Nothing to send", "Capture at least one screenshot first.")
            return
        endpoint = self.endpoint_var.get().strip()
        if not endpoint:
            messagebox.showwarning("Missing endpoint", "Enter the API endpoint URL.")
            return

        self._set_buttons_enabled(False)
        self.status_var.set("Sending to the solver…")
        self._set_answer("")

        # Network call off the UI thread so tkinter stays responsive.
        threading.Thread(target=self._send_worker, args=(endpoint,), daemon=True).start()

    def _send_worker(self, endpoint):
        try:
            images = []
            for img in self.screenshots:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                images.append(
                    {
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(buf.getvalue()).decode("ascii"),
                    }
                )

            resp = requests.post(endpoint, json={"images": images}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 400:
                # The API returns {"error": ...} on failure — surface it.
                try:
                    detail = resp.json().get("error", resp.text)
                except ValueError:
                    detail = resp.text
                self.root.after(0, lambda d=detail: self._on_error(d))
                return
            data = resp.json()
            answer = data.get("answer", "(No answer field in response.)")
            self.root.after(0, lambda: self._on_response(answer))
        except requests.exceptions.RequestException as e:
            self.root.after(0, lambda err=e: self._on_error(str(err)))
        except ValueError:
            self.root.after(0, lambda: self._on_error("Server did not return valid JSON."))

    def _on_response(self, answer):
        self._set_answer(answer)
        self.status_var.set("Done.")
        self._set_buttons_enabled(True)

    def _on_error(self, message):
        self.status_var.set("Request failed.")
        self._set_buttons_enabled(True)
        messagebox.showerror("Request failed", message)

    # ---------- rendering helpers ----------

    def _refresh(self):
        """Rebuild the thumbnail carousel and toggle button availability."""
        for child in self.carousel_inner.winfo_children():
            child.destroy()
        self._thumb_refs.clear()

        if not self.screenshots:
            ttk.Label(self.carousel_inner, text="No screenshots yet.").grid(row=0, column=0, padx=8, pady=8)
        else:
            for i, img in enumerate(self.screenshots):
                cell = ttk.Frame(self.carousel_inner, padding=4)
                cell.grid(row=0, column=i, padx=4, pady=2)

                thumb = img.copy()
                thumb.thumbnail(THUMB_SIZE)
                tk_thumb = ImageTk.PhotoImage(thumb)
                self._thumb_refs.append(tk_thumb)

                ttk.Label(cell, image=tk_thumb, relief="solid", borderwidth=1).pack()
                ttk.Label(cell, text=f"#{i + 1}").pack()
                ttk.Button(cell, text="Remove", width=8, command=lambda idx=i: self._remove(idx)).pack(pady=(2, 0))

        has_shots = bool(self.screenshots)
        # "New" is the primary entry point; "+ Add" and "Send" are only useful
        # once at least one screenshot exists.
        self.add_btn.state(["!disabled"] if has_shots else ["disabled"])
        self.send_btn.state(["!disabled"] if has_shots else ["disabled"])
        self.clear_btn.state(["!disabled"] if has_shots else ["disabled"])

    def _set_answer(self, text):
        self.answer_text.configure(state="normal")
        self.answer_text.delete("1.0", "end")
        self.answer_text.insert("1.0", text)
        self.answer_text.configure(state="disabled")

    def _set_buttons_enabled(self, enabled):
        flag = ["!disabled"] if enabled else ["disabled"]
        for btn in (self.new_btn, self.add_btn, self.send_btn, self.clear_btn):
            btn.state(flag)
        if enabled:
            self._refresh()  # re-apply the has-shots logic


def main():
    root = tk.Tk()
    QCMApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
