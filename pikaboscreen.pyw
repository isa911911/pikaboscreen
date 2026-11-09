"""
PikaboScreen - live mini-viewer for Windows.

Select an area of any window (for example a game's minimap) and watch it
in real time through a small always-on-top window, even when the original
window is covered by other apps.

Uses Windows Graphics Capture (via the windows-capture library).
Captures window images only. It does not read memory, inject code,
or send input to other programs.
"""

import ctypes
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

import numpy as np  # noqa: F401  (needed by windows-capture frames)
from PIL import Image, ImageTk
from windows_capture import WindowsCapture

APP_NAME = "PikaboScreen"
DEFAULT_TITLE = "Dota 2"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000  # clicking the mini window won't steal focus

BAR_H = 26
BAR_BG = "#1e1e1e"
BAR_FG = "#e6e6e6"


class PikaboScreen:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg="black")
        self.root.withdraw()

        title = simpledialog.askstring(
            APP_NAME,
            "Enter the exact title of the window you want to watch\n"
            "(as shown in its title bar, e.g. Dota 2):",
            initialvalue=DEFAULT_TITLE,
            parent=self.root,
        )
        if not title or not title.strip():
            self.root.destroy()
            return
        self.window_title = title.strip()

        # Borderless window: no title bar, no minimize/close buttons.
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.label = tk.Label(self.root, bg="black", bd=0)
        self.label.pack(fill="both", expand=True)

        self.lock = threading.Lock()
        self.full = None          # latest full frame (BGRA)
        self.frac = None          # selected area as fractions (x1, y1, x2, y2)
        self.fps_val = 30
        self.last_copy = 0.0
        self.control = None
        self._photo = None

        self.fps = tk.IntVar(value=30)
        self.alpha = tk.DoubleVar(value=1.0)
        self.topmost = tk.BooleanVar(value=True)

        self.hover = False
        self.busy = False         # true while dragging / resizing
        self._drag = (0, 0)

        self._build_menu()
        self._build_overlay()

        for w in (self.root, self.label):
            w.bind("<Button-3>", self._popup)
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
        self.root.bind("<Escape>", lambda e: self.quit())

        if not self.start_capture():
            self.root.destroy()
            return

        self.t_start = time.time()
        self.root.after(100, self.wait_first_frame)
        self.root.after(100, self.check_hover)
        self.root.mainloop()

    # ---------- capture ----------
    def start_capture(self):
        try:
            try:
                cap = WindowsCapture(
                    cursor_capture=False,
                    draw_border=False,
                    window_name=self.window_title,
                )
            except TypeError:  # older library version
                cap = WindowsCapture(window_name=self.window_title)

            @cap.event
            def on_frame_arrived(frame, capture_control):
                now = time.perf_counter()
                if now - self.last_copy < 1.0 / max(self.fps_val, 1):
                    return
                self.last_copy = now
                buf = frame.frame_buffer.copy()
                with self.lock:
                    self.full = buf

            @cap.event
            def on_closed():
                pass

            self.control = cap.start_free_threaded()
            return True
        except Exception as e:
            messagebox.showerror(
                APP_NAME,
                f"Could not capture the window '{self.window_title}'.\n"
                "Make sure the app is running and not minimized, and that "
                "the title matches exactly.\n\n"
                f"{e}",
            )
            return False

    def wait_first_frame(self):
        with self.lock:
            ready = self.full is not None
        if ready:
            self.select_region()
            if self.frac is None:
                self.quit()
                return
            self.update_frame()
        elif time.time() - self.t_start > 10:
            messagebox.showerror(
                APP_NAME,
                f"No image received from '{self.window_title}'.\n"
                "Make sure the window is not minimized, then try again.",
            )
            self.quit()
        else:
            self.root.after(100, self.wait_first_frame)

    # ---------- menu ----------
    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Select area again", command=self.select_region)

        fps_menu = tk.Menu(m, tearoff=0)
        for f in (5, 10, 15, 30, 60):
            fps_menu.add_radiobutton(
                label=f"{f} FPS", value=f, variable=self.fps,
                command=lambda: setattr(self, "fps_val", self.fps.get()),
            )
        m.add_cascade(label="Speed (FPS)", menu=fps_menu)

        a_menu = tk.Menu(m, tearoff=0)
        for a in (1.0, 0.85, 0.7, 0.5, 0.3):
            a_menu.add_radiobutton(
                label=f"{int(a * 100)}%", value=a, variable=self.alpha,
                command=lambda: self.root.attributes("-alpha", self.alpha.get()),
            )
        m.add_cascade(label="Opacity", menu=a_menu)

        m.add_checkbutton(
            label="Always on top", variable=self.topmost,
            command=lambda: self.root.attributes("-topmost", self.topmost.get()),
        )
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        self.menu = m

    def _popup(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---------- hover overlay (bar + resize grip) ----------
    def _build_overlay(self):
        self.bar = tk.Frame(self.root, bg=BAR_BG, height=BAR_H)

        title = tk.Label(self.bar, text=APP_NAME, bg=BAR_BG, fg=BAR_FG,
                         font=("Segoe UI", 9), anchor="w")
        title.pack(side="left", padx=8)

        def btn(text, cmd):
            b = tk.Label(self.bar, text=text, bg=BAR_BG, fg=BAR_FG,
                         font=("Segoe UI", 10), width=3, cursor="hand2")
            b.pack(side="right", fill="y")
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.configure(bg="#444444"))
            b.bind("<Leave>", lambda e: b.configure(bg=BAR_BG))
            return b

        btn("\u2715", self.quit)  # close
        self.menu_btn = btn("\u2630", self._menu_from_button)
        btn("\u25A3", self.select_region)  # select area again

        # dragging by the bar title area
        for w in (self.bar, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>", self._popup)

        self.grip = tk.Label(self.root, text="\u25E2", bg=BAR_BG, fg=BAR_FG,
                             font=("Segoe UI", 10), cursor="size_nw_se")
        self.grip.bind("<ButtonPress-1>", self._resize_start)
        self.grip.bind("<B1-Motion>", self._resize_move)
        self.grip.bind("<ButtonRelease-1>", self._drag_end)

    def _menu_from_button(self):
        x = self.menu_btn.winfo_rootx()
        y = self.menu_btn.winfo_rooty() + BAR_H
        self.menu.tk_popup(x, y)

    def check_hover(self):
        try:
            px, py = self.root.winfo_pointerxy()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            inside = rx <= px < rx + w and ry <= py < ry + h
            show = inside or self.busy
            if show and not self.hover:
                self.bar.place(x=0, y=0, relwidth=1, height=BAR_H)
                self.grip.place(relx=1.0, rely=1.0, anchor="se")
                self.bar.lift()
                self.grip.lift()
                self.hover = True
            elif not show and self.hover:
                self.bar.place_forget()
                self.grip.place_forget()
                self.hover = False
        except Exception:
            pass
        self.root.after(100, self.check_hover)

    # ---------- move / resize ----------
    def _drag_start(self, e):
        self.busy = True
        self._drag = (e.x_root - self.root.winfo_x(),
                      e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        ox, oy = self._drag
        self.root.geometry(f"+{e.x_root - ox}+{e.y_root - oy}")

    def _drag_end(self, e):
        self.busy = False

    def _resize_start(self, e):
        self.busy = True

    def _resize_move(self, e):
        w = max(e.x_root - self.root.winfo_rootx(), 120)
        h = max(e.y_root - self.root.winfo_rooty(), 80)
        self.root.geometry(f"{w}x{h}")

    # ---------- area selection ----------
    def select_region(self):
        with self.lock:
            full = self.full
        if full is None:
            return
        H, W = full.shape[:2]
        pil = Image.fromarray(full[..., [2, 1, 0]])

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        s = min(sw * 0.85 / W, sh * 0.85 / H, 1.0)
        dw, dh = int(W * s), int(H * s)

        self.root.withdraw()
        top = tk.Toplevel(self.root)
        top.title("Drag to select the area to watch  (Esc = cancel)")
        top.attributes("-topmost", True)
        photo = ImageTk.PhotoImage(pil.resize((dw, dh), Image.BILINEAR))
        canvas = tk.Canvas(top, width=dw, height=dh, cursor="crosshair",
                           highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo

        st = {"x0": 0, "y0": 0, "rect": None, "res": None}

        def press(e):
            st["x0"], st["y0"] = e.x, e.y
            st["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="#00ff88", width=2
            )

        def drag(e):
            if st["rect"]:
                canvas.coords(st["rect"], st["x0"], st["y0"], e.x, e.y)

        def release(e):
            x1, x2 = sorted((max(0, min(st["x0"], dw)), max(0, min(e.x, dw))))
            y1, y2 = sorted((max(0, min(st["y0"], dh)), max(0, min(e.y, dh))))
            if (x2 - x1) >= 10 and (y2 - y1) >= 10:
                st["res"] = (x1, y1, x2, y2)
            top.destroy()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        top.bind("<Escape>", lambda e: top.destroy())
        top.focus_force()
        top.wait_window()

        if st["res"]:
            x1, y1, x2, y2 = st["res"]
            self.frac = (x1 / dw, y1 / dh, x2 / dw, y2 / dh)
            cw, ch = (x2 - x1) / s, (y2 - y1) / s  # original size in pixels
            k = min(640 / cw, 360 / ch, 1.0)
            self.root.geometry(f"{max(int(cw * k), 160)}x{max(int(ch * k), 90)}")

        if self.frac is not None:
            self.root.deiconify()
            self.root.attributes("-topmost", self.topmost.get())
            self.root.update_idletasks()
            self._no_activate()

    def _no_activate(self):
        """Clicking the mini window won't move focus away from the game."""
        try:
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        except Exception:
            pass

    # ---------- display loop ----------
    def update_frame(self):
        t0 = time.perf_counter()
        try:
            with self.lock:
                full = self.full
            if full is not None and self.frac is not None:
                H, W = full.shape[:2]
                fx1, fy1, fx2, fy2 = self.frac
                x1, x2 = int(fx1 * W), int(fx2 * W)
                y1, y2 = int(fy1 * H), int(fy2 * H)
                if x2 - x1 >= 2 and y2 - y1 >= 2:
                    img = Image.fromarray(full[y1:y2, x1:x2][..., [2, 1, 0]])
                    w, h = img.size
                    lw = max(self.label.winfo_width(), 1)
                    lh = max(self.label.winfo_height(), 1)
                    k = min(lw / w, lh / h)
                    nw, nh = max(int(w * k), 1), max(int(h * k), 1)
                    if (nw, nh) != (w, h):
                        img = img.resize((nw, nh), Image.BILINEAR)
                    self._photo = ImageTk.PhotoImage(img)
                    self.label.configure(image=self._photo)
        except Exception as e:
            print("Error:", e)

        elapsed = int((time.perf_counter() - t0) * 1000)
        delay = max(1, int(1000 / max(self.fps.get(), 1)) - elapsed)
        self.root.after(delay, self.update_frame)

    def quit(self):
        try:
            if self.control:
                self.control.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    PikaboScreen()
