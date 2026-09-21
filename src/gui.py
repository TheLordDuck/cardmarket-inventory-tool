"""Tkinter GUI for the Cardmarket CLI (`src/main.py`).

Doesn't reimplement any Cardmarket/browser logic -- it just exposes the four
CLI commands (`login`, `reprice`, `export`, `import`) and their flags as
widgets, and shells out to `python -m src.main <command> ...` (the same
commands documented in the README), streaming stdout/stderr into a log panel.

Run it with:

    .venv\\Scripts\\python.exe -m src.gui
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .cardmarket.browser_client import DEFAULT_PRICE_SOURCE, PRICE_SOURCE_LABELS
from .config import ROOT_DIR

# Combobox display labels for `reprice --price-source`, derived from the
# browser client's own PRICE_SOURCE_LABELS (the literal stat names shown on
# a Cardmarket product page) so the two can't drift out of sync.
PRICE_SOURCE_DISPLAY: dict[str, str] = {
    key: (f"{label} (default)" if key == DEFAULT_PRICE_SOURCE else label)
    for key, label in PRICE_SOURCE_LABELS.items()
}


def build_login_args(debug: bool) -> list[str]:
    args = ["login"]
    if debug:
        args.append("--debug-browser")
    return args


def build_reprice_args(
    dry_run: bool, debug: bool, inspect_modal: bool, limit: str,
    price_source: str = DEFAULT_PRICE_SOURCE, adjustment_pct: str = "",
) -> list[str]:
    args = ["reprice"]
    if dry_run:
        args.append("--dry-run")
    if debug:
        args.append("--debug-browser")
    if inspect_modal:
        args.append("--inspect-modal")
    limit = limit.strip()
    if limit:
        args += ["--limit", limit]
    if price_source and price_source != DEFAULT_PRICE_SOURCE:
        args += ["--price-source", price_source]
    adjustment_pct = adjustment_pct.strip()
    if adjustment_pct:
        args += ["--adjustment-pct", adjustment_pct]
    return args


def build_export_args(output: str, debug: bool) -> list[str]:
    args = ["export"]
    output = output.strip()
    if output:
        args += ["--output", output]
    if debug:
        args.append("--debug-browser")
    return args


def build_import_args(input_path: str, dry_run: bool, debug: bool, inspect_modal: bool, limit: str) -> list[str]:
    args = ["import"]
    input_path = input_path.strip()
    if input_path:
        args += ["--input", input_path]
    if dry_run:
        args.append("--dry-run")
    if debug:
        args.append("--debug-browser")
    if inspect_modal:
        args.append("--inspect-modal")
    limit = limit.strip()
    if limit:
        args += ["--limit", limit]
    return args


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cardmarket Inventory Tool")
        self.geometry("760x560")
        self.minsize(640, 480)

        self._proc: subprocess.Popen | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()

        self._build_widgets()
        self.after(100, self._poll_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI construction -------------------------------------------------

    def _build_widgets(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="x", padx=10, pady=(10, 0))

        notebook.add(self._build_login_tab(notebook), text="Login")
        notebook.add(self._build_reprice_tab(notebook), text="Reprice (auto, Cardmarket price)")
        notebook.add(self._build_export_tab(notebook), text="Export stock to CSV")
        notebook.add(self._build_import_tab(notebook), text="Import edited CSV")

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=(8, 0))
        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")
        self.cancel_button = ttk.Button(status_frame, text="Cancel run", command=self._cancel_run, state="disabled")
        self.cancel_button.pack(side="right")
        ttk.Button(status_frame, text="Clear log", command=self._clear_log).pack(side="right", padx=(0, 6))

        log_frame = ttk.LabelFrame(self, text="Output")
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def _build_login_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)

        ttk.Label(
            frame,
            text="Launches Chrome (if needed) and logs in with CARDMARKET_USERNAME/\n"
                 "PASSWORD from .env. If 2FA/verification appears, complete it in the\n"
                 "browser window -- this waits for you (up to LOGIN_WAIT_TIMEOUT_SECONDS)\n"
                 "and continues on its own once you're done. Run this first, or just let\n"
                 "Reprice/Export/Import trigger it automatically.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self.login_debug = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Debug (dump page HTML/screenshots to reports/)", variable=self.login_debug) \
            .grid(row=1, column=0, columnspan=3, sticky="w")

        ttk.Button(frame, text="Login", command=self._run_login).grid(row=2, column=0, sticky="w", pady=(14, 0))
        return frame

    def _build_reprice_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)

        ttk.Label(
            frame,
            text="Reads each card's own Cardmarket price (pick trend/30-day/7-day/1-day\n"
                 "average below), applies a per-condition multiplier and your own +/-\n"
                 "adjustment %, and writes the result back.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self.reprice_dry_run = tk.BooleanVar(value=True)
        self.reprice_debug = tk.BooleanVar(value=False)
        self.reprice_inspect_modal = tk.BooleanVar(value=False)
        self.reprice_limit = tk.StringVar(value="")
        self.reprice_price_source_label = tk.StringVar(value=PRICE_SOURCE_DISPLAY[DEFAULT_PRICE_SOURCE])
        self.reprice_adjustment_pct = tk.StringVar(value="")

        ttk.Checkbutton(frame, text="Dry run (don't change any prices)", variable=self.reprice_dry_run) \
            .grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frame, text="Debug (dump page HTML/screenshots to reports/)", variable=self.reprice_debug) \
            .grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            frame, text="Inspect modal only (safe -- opens/closes the edit form, changes nothing)",
            variable=self.reprice_inspect_modal,
        ).grid(row=3, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="Price source:").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            frame, textvariable=self.reprice_price_source_label, state="readonly", width=28,
            values=list(PRICE_SOURCE_DISPLAY.values()),
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Label(frame, text="Adjustment % (optional, e.g. +5 or -5):").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.reprice_adjustment_pct, width=10).grid(row=5, column=1, sticky="w", pady=(6, 0))

        ttk.Label(frame, text="Limit (max articles this run, optional):").grid(row=6, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.reprice_limit, width=10).grid(row=6, column=1, sticky="w", pady=(10, 0))

        button_row = ttk.Frame(frame)
        button_row.grid(row=7, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Button(button_row, text="Run reprice", command=self._run_reprice).pack(side="left")
        ttk.Button(button_row, text="Generate price changes CSV only (dry run)",
                   command=self._run_reprice_csv_only).pack(side="left", padx=(8, 0))
        return frame

    def _build_export_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)

        ttk.Label(
            frame,
            text="Scrapes your current stock into a CSV. Open it afterwards and edit\n"
                 "only the 'price' column, then use the Import tab.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self.export_output = tk.StringVar(value="")
        self.export_debug = tk.BooleanVar(value=False)

        ttk.Label(frame, text="Output CSV path (optional):").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.export_output, width=48).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Button(frame, text="Browse...", command=self._pick_export_output).grid(row=1, column=2, padx=(6, 0), pady=(10, 0))

        ttk.Checkbutton(frame, text="Debug (dump page HTML/screenshots to reports/)", variable=self.export_debug) \
            .grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Button(frame, text="Run export", command=self._run_export).grid(row=3, column=0, sticky="w", pady=(14, 0))
        return frame

    def _build_import_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)

        ttk.Label(
            frame,
            text="Applies the 'price' column from a previously exported (and edited)\n"
                 "CSV back into Cardmarket via the browser.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self.import_input = tk.StringVar(value="")
        self.import_dry_run = tk.BooleanVar(value=True)
        self.import_debug = tk.BooleanVar(value=False)
        self.import_inspect_modal = tk.BooleanVar(value=False)
        self.import_limit = tk.StringVar(value="")

        ttk.Label(frame, text="Input CSV (optional, defaults to newest export):").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.import_input, width=48).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Button(frame, text="Browse...", command=self._pick_import_input).grid(row=1, column=2, padx=(6, 0), pady=(10, 0))

        ttk.Checkbutton(frame, text="Dry run (don't change any prices)", variable=self.import_dry_run) \
            .grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(frame, text="Debug (dump page HTML/screenshots to reports/)", variable=self.import_debug) \
            .grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            frame, text="Inspect modal only (safe -- opens/closes the edit form, changes nothing)",
            variable=self.import_inspect_modal,
        ).grid(row=4, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="Limit (max articles this run, optional):").grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.import_limit, width=10).grid(row=5, column=1, sticky="w", pady=(10, 0))

        ttk.Button(frame, text="Run import", command=self._run_import).grid(row=6, column=0, sticky="w", pady=(14, 0))
        return frame

    # ---- file pickers ------------------------------------------------

    def _pick_export_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save stock export as", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")], initialdir=str(ROOT_DIR / "reports"),
        )
        if path:
            self.export_output.set(path)

    def _pick_import_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select edited stock CSV", filetypes=[("CSV files", "*.csv")],
            initialdir=str(ROOT_DIR / "reports"),
        )
        if path:
            self.import_input.set(path)

    # ---- run handlers --------------------------------------------------

    def _run_login(self) -> None:
        args = build_login_args(self.login_debug.get())
        self._start_run(args)

    def _reprice_price_source_key(self) -> str:
        label = self.reprice_price_source_label.get()
        for key, value in PRICE_SOURCE_DISPLAY.items():
            if value == label:
                return key
        return DEFAULT_PRICE_SOURCE

    def _run_reprice(self) -> None:
        args = build_reprice_args(
            self.reprice_dry_run.get(), self.reprice_debug.get(),
            self.reprice_inspect_modal.get(), self.reprice_limit.get(),
            self._reprice_price_source_key(), self.reprice_adjustment_pct.get(),
        )
        self._start_run(args)

    def _run_reprice_csv_only(self) -> None:
        """Always forces --dry-run regardless of the checkbox above -- a
        one-click way to get reports/pricing_<timestamp>.csv without risking
        an accidental live run.
        """
        args = build_reprice_args(
            True, self.reprice_debug.get(), self.reprice_inspect_modal.get(), self.reprice_limit.get(),
            self._reprice_price_source_key(), self.reprice_adjustment_pct.get(),
        )
        self._start_run(args)

    def _run_export(self) -> None:
        args = build_export_args(self.export_output.get(), self.export_debug.get())
        self._start_run(args)

    def _run_import(self) -> None:
        args = build_import_args(
            self.import_input.get(), self.import_dry_run.get(), self.import_debug.get(),
            self.import_inspect_modal.get(), self.import_limit.get(),
        )
        self._start_run(args)

    # ---- process management --------------------------------------------

    def _start_run(self, args: list[str]) -> None:
        if self._proc is not None and self._proc.poll() is None:
            messagebox.showwarning("Already running", "A command is already running -- wait for it to finish or cancel it.")
            return

        cmd = [sys.executable, "-m", "src.main", *args]
        self._append_log(f"$ {' '.join(cmd)}\n")
        self.status_var.set(f"Running: {' '.join(args)}")
        self.cancel_button.config(state="normal")

        thread = threading.Thread(target=self._run_subprocess, args=(cmd,), daemon=True)
        thread.start()

    def _run_subprocess(self, cmd: list[str]) -> None:
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=str(ROOT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in self._proc.stdout:
                self._log_queue.put(line)
            self._proc.wait()
            self._log_queue.put(f"\n[exit code {self._proc.returncode}]\n")
        except Exception as exc:
            self._log_queue.put(f"\n[failed to launch: {exc}]\n")
        finally:
            self._proc = None
            self._log_queue.put("__DONE__")

    def _cancel_run(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            self._append_log("\n[cancelled by user]\n")

    # ---- log panel -------------------------------------------------------

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                if line == "__DONE__":
                    self.status_var.set("Idle.")
                    self.cancel_button.config(state="disabled")
                else:
                    self._append_log(line)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _on_close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            if not messagebox.askyesno("Command running", "A command is still running. Cancel it and quit?"):
                return
            self._proc.terminate()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
