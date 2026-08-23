"""Window front end, so converting a book does not need a terminal.

Tkinter only - nothing to install. The conversion itself is `cli.main`, run on a
worker thread with its output piped into the log pane.
"""

import ctypes
import os
import queue
import sys
import threading


def _enable_dpi_awareness() -> None:
    """Windows stretches a DPI-unaware window as a bitmap, which is what makes
    the text look soft at any display scale above 100%. This has to run before
    the first Tk window exists or the process keeps the unaware setting."""
    if sys.platform != "win32":
        return
    for call in (lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
                 lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            call()
            return
        except Exception:
            continue


_enable_dpi_awareness()

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import archive
import cli
import sources

SUPPORTED_TYPES = [("Novel files", "*.txt *.epub *.EPUB *.zip"), ("All files", "*.*")]
UI_FONT = ("Segoe UI", 9)
LOG_FONT = ("Consolas", 9)


def default_folder() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else os.path.expanduser("~")


def default_output() -> str:
    return os.path.join(default_folder(), "areader-import.zip")


class _Pipe:
    """Stands in for sys.stdout so print() from cli lands in the log pane."""

    def __init__(self, sink: queue.Queue):
        self.sink = sink

    def write(self, text: str) -> int:
        if text:
            self.sink.put(text)
        return len(text)

    def flush(self) -> None:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.inputs: list[str] = []
        self.messages: queue.Queue = queue.Queue()
        self.running = False
        self.last_output = ""
        self.out_touched = False

        # Tk sizes fonts from its scaling factor, but a geometry given in pixels
        # would come out physically smaller once the process is DPI aware.
        scale = root.winfo_fpixels("1i") / 96.0
        root.title("AReader Import Builder")
        root.geometry(f"{int(820 * scale)}x{int(660 * scale)}")
        root.minsize(int(700 * scale), int(560 * scale))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        self._build_inputs()
        self._build_output()
        self._build_options()
        self._build_log()
        self._build_actions()
        self.root.after(80, self._drain)

    # ---------------------------------------------------------------- layout
    def _build_inputs(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" Files ", padding=8)
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        frame.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(frame, height=5, font=UI_FONT,
                                  selectmode=tk.EXTENDED, activestyle="none")
        self.listbox.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.config(yscrollcommand=scroll.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=2, sticky="n", padx=(8, 0))
        ttk.Button(buttons, text="Add files", width=11, command=self.add_files).pack(pady=2)
        ttk.Button(buttons, text="Add folder", width=11, command=self.add_folder).pack(pady=2)
        ttk.Button(buttons, text="Remove", width=11, command=self.remove_selected).pack(pady=2)
        ttk.Button(buttons, text="Clear", width=11, command=self.clear_inputs).pack(pady=2)

        ttk.Label(frame, text="Add .txt, .epub or a .zip of volumes. A folder adds every novel file inside it.",
                  font=UI_FONT, foreground="#666").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_output(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" Save to ", padding=8)
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        frame.columnconfigure(0, weight=1)

        self.out_var = tk.StringVar(value=default_output())
        entry = ttk.Entry(frame, textvariable=self.out_var, font=UI_FONT)
        entry.grid(row=0, column=0, sticky="ew")
        # Once the name is typed by hand, stop suggesting over it.
        entry.bind("<Key>", lambda _event: setattr(self, "out_touched", True))
        ttk.Button(frame, text="Browse", width=11, command=self.pick_output).grid(
            row=0, column=1, padx=(8, 0))
        self.out_hint = ttk.Label(frame, text="Named after the first work once you add a file.",
                                  font=UI_FONT, foreground="#666")
        self.out_hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_options(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" Options ", padding=8)
        frame.grid(row=2, column=0, sticky="ew", padx=10, pady=6)

        self.dry_run = tk.BooleanVar(value=False)
        self.separate = tk.BooleanVar(value=False)
        self.verbose = tk.BooleanVar(value=False)

        ttk.Checkbutton(frame, text="Preview only (write nothing)", variable=self.dry_run).grid(
            row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Checkbutton(frame, text="One zip per work", variable=self.separate).grid(
            row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Checkbutton(frame, text="List every chapter", variable=self.verbose).grid(
            row=1, column=0, sticky="w", pady=(6, 0))

    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self.root, text=" Output ", padding=8)
        frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=6)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log = tk.Text(frame, wrap="none", font=LOG_FONT, height=14,
                           background="#1e1e1e", foreground="#dcdcdc",
                           insertbackground="#dcdcdc", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        y = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        y.grid(row=0, column=1, sticky="ns")
        x = ttk.Scrollbar(frame, orient="horizontal", command=self.log.xview)
        x.grid(row=1, column=0, sticky="ew")
        self.log.config(yscrollcommand=y.set, xscrollcommand=x.set)
        self.log.tag_config("bad", foreground="#f48771")
        self.log.tag_config("good", foreground="#89d185")

    def _build_actions(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        frame.grid(row=4, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        self.run_button = ttk.Button(frame, text="Convert", width=14, command=self.start)
        self.run_button.grid(row=0, column=0)
        self.status = ttk.Label(frame, text="Add a file to start.", font=UI_FONT)
        self.status.grid(row=0, column=1, sticky="w", padx=10)
        self.open_button = ttk.Button(frame, text="Open folder", width=11,
                                      command=self.open_output, state="disabled")
        self.open_button.grid(row=0, column=2)

    # ----------------------------------------------------------------- input
    def _expand(self) -> "list[str]":
        """The novel files behind the chosen paths, same rule the CLI uses."""
        files = []
        for path in self.inputs:
            if os.path.isdir(path):
                for entry in sorted(os.listdir(path)):
                    full = os.path.join(path, entry)
                    if os.path.isfile(full) and sources.is_supported(entry):
                        files.append(full)
            elif os.path.isfile(path):
                files.append(path)
        return files

    def _suggest_output(self) -> None:
        """Name the zip after the work, reading the title straight off the
        filename so nothing has to be opened to fill this in."""
        if self.out_touched:
            return
        files = self._expand()
        if not files:
            name = "areader-import"
        else:
            name = archive.suggest_zip_name(files)
        folder = os.path.dirname(self.out_var.get()) or default_folder()
        self.out_var.set(os.path.join(folder, name + ".zip"))

    def _add(self, paths) -> None:
        for path in paths:
            path = os.path.normpath(path)
            if path not in self.inputs:
                self.inputs.append(path)
                self.listbox.insert(tk.END, path)
        self._suggest_output()
        self._update_status()

    def add_files(self) -> None:
        self._add(filedialog.askopenfilenames(title="Choose novel files", filetypes=SUPPORTED_TYPES))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose a folder")
        if folder:
            self._add([folder])

    def remove_selected(self) -> None:
        for index in sorted(self.listbox.curselection(), reverse=True):
            self.listbox.delete(index)
            del self.inputs[index]
        self._suggest_output()
        self._update_status()

    def clear_inputs(self) -> None:
        self.listbox.delete(0, tk.END)
        self.inputs.clear()
        self._suggest_output()
        self._update_status()

    def pick_output(self) -> None:
        chosen = filedialog.asksaveasfilename(
            title="Save as", defaultextension=".zip",
            initialfile=os.path.basename(self.out_var.get()),
            initialdir=os.path.dirname(self.out_var.get()),
            filetypes=[("ZIP file", "*.zip")])
        if chosen:
            self.out_touched = True
            self.out_var.set(os.path.normpath(chosen))

    def _update_status(self) -> None:
        if self.running:
            return
        count = len(self.inputs)
        self.status.config(text="Add a file to start." if not count else f"{count} selected.")

    def open_output(self) -> None:
        folder = os.path.dirname(self.last_output) or "."
        if os.path.isdir(folder):
            os.startfile(folder)          # noqa: S606 - Windows shell open

    # ------------------------------------------------------------------- run
    def _write(self, text: str, tag: str = "") -> None:
        self.log.config(state="normal")
        self.log.insert(tk.END, text, tag or ())
        self.log.see(tk.END)
        self.log.config(state="disabled")

    def start(self) -> None:
        if self.running:
            return
        if not self.inputs:
            messagebox.showinfo("AReader Import Builder", "Add a file or folder first.")
            return
        out = self.out_var.get().strip()
        if not self.dry_run.get():
            if not out:
                messagebox.showinfo("AReader Import Builder", "Choose where to save.")
                return
            folder = os.path.dirname(os.path.abspath(out))
            if not os.path.isdir(folder):
                messagebox.showerror("AReader Import Builder",
                                     f"That folder does not exist:\n{folder}")
                return

        argv = list(self.inputs) + ["-o", out]
        if self.dry_run.get():
            argv.append("--dry-run")
        if self.separate.get():
            argv.append("--separate")
        if self.verbose.get():
            argv.append("--verbose")

        self.running = True
        self.last_output = out
        self.run_button.config(state="disabled")
        self.open_button.config(state="disabled")
        self.status.config(text="Converting...")
        self.log.config(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.config(state="disabled")
        threading.Thread(target=self._work, args=(argv,), daemon=True).start()

    def _work(self, argv) -> None:
        pipe = _Pipe(self.messages)
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = pipe
        try:
            code = cli.main(argv)
        except Exception as error:                        # noqa: BLE001 - show it, do not die
            pipe.write(f"\nError: {type(error).__name__}: {error}\n")
            code = 1
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        self.messages.put(("done", code))

    def _drain(self) -> None:
        while True:
            try:
                item = self.messages.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                self._finish(item[1])
            else:
                self._write(item)
        self.root.after(80, self._drain)

    def _finish(self, code: int) -> None:
        self.running = False
        self.run_button.config(state="normal")
        if code == 0 and not self.dry_run.get():
            self._write("\nDone. Move the zip to your phone, then open the "
                        "Imported tab and tap +.\n", "good")
            self.open_button.config(state="normal")
            self.status.config(text="Done")
        elif code == 0:
            self._write("\nPreview only - nothing was written.\n", "good")
            self.status.config(text="Preview done")
        else:
            self._write("\nNothing was converted. Check the output above.\n", "bad")
            self.status.config(text="Failed")


def main() -> None:
    root = tk.Tk()
    # Points -> pixels at the real display DPI, so text is rendered sharp rather
    # than drawn small and stretched.
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    try:
        root.call("ttk::style", "theme", "use", "vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
