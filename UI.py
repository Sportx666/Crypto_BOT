"""
SPORTYX Crypto Bot  —  Modern Dark UI
======================================
Full drop-in replacement for UI.py.

All public methods, properties and widget attributes required by
  main.py / scheduler.py / force_close.py / place_trade.py / utility.py
are preserved exactly.

Requires:  pip install customtkinter matplotlib
"""

# ── stdlib ────────────────────────────────────────────────────────────────────
from collections import defaultdict
from datetime import datetime
import re
import threading
import time

# ── tkinter (always available) ───────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
import tkinter.messagebox as messagebox

# ── customtkinter ─────────────────────────────────────────────────────────────
try:
    import customtkinter as ctk
    from customtkinter import CTkFont
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
except ImportError:
    messagebox.showerror(
        "Missing dependency",
        "customtkinter is not installed.\n\nRun:  pip install customtkinter\n\nThen restart."
    )
    raise SystemExit(1)

# ── matplotlib ────────────────────────────────────────────────────────────────
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import matplotlib as mpl
    mpl.rcParams.update({
        "figure.facecolor":  "#0f0f1a",
        "axes.facecolor":    "#0f0f1a",
        "axes.edgecolor":    "#2d2d4a",
        "text.color":        "#e2e8f0",
        "xtick.color":       "#8892a4",
        "ytick.color":       "#8892a4",
        "grid.color":        "#1c1c2e",
    })
except ImportError:
    messagebox.showerror(
        "Missing dependency",
        "matplotlib is not installed.\n\nRun:  pip install matplotlib\n\nThen restart."
    )
    raise SystemExit(1)

# ── project imports ───────────────────────────────────────────────────────────
from misc.config import *
from trade.force_close import close_orders
from scheduler import scheduled_routine
from core.shared_state import get_gui_instance
from utilities.utility import save_blacklist, save_trade_counter, update_trading_table


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Colour Palette
# ╚══════════════════════════════════════════════════════════════════════════════
C = {
    "bg0":       "#090912",   # deepest window background
    "bg1":       "#0f0f1a",   # container / sidebar
    "bg2":       "#141420",   # card / frame
    "bg3":       "#1c1c2e",   # elevated card
    "bg4":       "#252538",   # hover / selected
    "border":    "#2d2d4a",
    "accent":    "#4cc9f0",
    "success":   "#06d6a0",
    "danger":    "#ef233c",
    "warning":   "#ffd60a",
    "text":      "#e2e8f0",
    "text_dim":  "#8892a4",
    "text_muted":"#4a5568",
}


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Dark ttk Treeview style  (applied once when root is created)
# ╚══════════════════════════════════════════════════════════════════════════════
def _apply_dark_treeview_style(root):
    s = ttk.Style(root)
    s.theme_use("default")

    s.configure("Dark.Treeview",
        background=C["bg2"],
        foreground=C["text"],
        fieldbackground=C["bg2"],
        rowheight=26,
        borderwidth=0,
        relief="flat",
        font=("Consolas", 10),
    )
    s.configure("Dark.Treeview.Heading",
        background=C["bg1"],
        foreground=C["accent"],
        relief="flat",
        borderwidth=0,
        font=("Segoe UI", 9, "bold"),
    )
    s.map("Dark.Treeview",
        background=[("selected", C["bg4"])],
        foreground=[("selected", "#ffffff")],
    )
    s.map("Dark.Treeview.Heading",
        background=[("active", C["bg3"])],
        relief=[("active", "flat")],
    )

    # Scrollbar
    s.configure("Dark.Vertical.TScrollbar",
        background=C["bg3"], troughcolor=C["bg1"],
        borderwidth=0, arrowcolor=C["text_dim"],
    )
    s.configure("Dark.Horizontal.TScrollbar",
        background=C["bg3"], troughcolor=C["bg1"],
        borderwidth=0, arrowcolor=C["text_dim"],
    )


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  CompatButton  —  CTkButton with full tkinter .config() / state compatibility
# ╚══════════════════════════════════════════════════════════════════════════════
_STATE_MAP = {
    "active":   "normal",
    "normal":   "normal",
    "disabled": "disabled",
}

class CompatButton(ctk.CTkButton):
    """
    CTkButton that accepts tkinter-style calls:
        btn.config(state=tk.DISABLED)   →  configure(state="disabled")
        btn.config(state=tk.ACTIVE)     →  configure(state="normal")
        btn.config(state=tk.NORMAL)     →  configure(state="normal")
    """
    def configure(self, **kw):
        if "state" in kw:
            kw["state"] = _STATE_MAP.get(str(kw["state"]).lower(), "normal")
        super().configure(**kw)

    # tkinter alias — external code calls .config(state=...)
    config = configure


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  CryptoBotGUI  —  Main GUI class (exact same signature as original)
# ╚══════════════════════════════════════════════════════════════════════════════
class CryptoBotGUI:
    """
    SPORTYX Crypto Bot — main GUI.
    Called as:  gui = CryptoBotGUI(root)   where root is ctk.CTk()
    """

    def __init__(self, root):
        self.root = root

        # ── configure root window ─────────────────────────────────────────────
        self.root.title("SPORTYX Crypto Bot")
        self.root.geometry("1280x820")
        self.root.minsize(900, 600)
        self.root.configure(fg_color=C["bg0"])
        try:
            self.root.iconbitmap(icon_file)
        except Exception:
            pass

        _apply_dark_treeview_style(self.root)

        # ── log file paths ────────────────────────────────────────────────────
        self.log_file_path          = f'{script_dir}\\logs\\trading_bot.log'
        self.trade_log_path         = f'{script_dir}\\logs\\trade_logs.log'
        self.detailed_trade_log_path= f'{script_dir}\\logs\\detailed_trade_logs.log'
        self.skip_trade_log_path    = f'{script_dir}\\logs\\skip_trade_logs.log'

        # ── internal state ────────────────────────────────────────────────────
        self._is_running = False

        # ── build bottom bar FIRST so pack(side="bottom") reserves the space ────
        self._build_bottom_bar()   # bar.pack(side="bottom") called inside

        # ── tab view fills remaining space ────────────────────────────────────
        # Pass command= here — CTkTabview appends it to its own internal
        # tab-switch handler, so switching still works AND we get the callback.
        self.notebook = ctk.CTkTabview(
            self.root,
            fg_color=C["bg1"],
            segmented_button_fg_color=C["bg0"],
            segmented_button_selected_color=C["accent"],
            segmented_button_selected_hover_color="#3ab5db",
            segmented_button_unselected_color=C["bg0"],
            segmented_button_unselected_hover_color=C["bg3"],
            text_color=C["text"],
            corner_radius=0,
            command=self._on_ctk_tab_change,   # ← correct hook point
        )
        self.notebook.pack(fill="both", expand=True)
        self.notebook.add("Current Trades")
        self.notebook.add("Log Files")
        self.notebook.add("P&L and Trade Analysis")
        self.notebook.add("Backtest")

        # ── build each tab ────────────────────────────────────────────────────
        self._build_current_trades_tab()
        self._build_log_files_tab()
        self._build_pnl_tab()
        self._build_backtest_tab()


    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Tab 1: Current Trades
    # ╚══════════════════════════════════════════════════════════════════════════
    def _build_current_trades_tab(self):
        tab = self.notebook.tab("Current Trades")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=3)   # trade table
        tab.rowconfigure(1, weight=2)   # console + graph

        # ── orders table ──────────────────────────────────────────────────────
        table_frame = ctk.CTkFrame(tab, fg_color=C["bg2"], corner_radius=8)
        table_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 3))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        cols = ("Date/Time", "Pair", "Order ID", "Type", "Side", "Price", "Quantity", "Status")
        self.tradinglist_table = ttk.Treeview(
            table_frame, columns=cols, show="headings",
            style="Dark.Treeview", height=10,
        )
        col_widths = {"Date/Time": 130, "Pair": 90, "Order ID": 120,
                      "Type": 90, "Side": 60, "Price": 100, "Quantity": 100, "Status": 100}
        for col in cols:
            self.tradinglist_table.heading(col, text=col, anchor="w")
            self.tradinglist_table.column(col, width=col_widths.get(col, 90), anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                            command=self.tradinglist_table.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tradinglist_table.configure(yscrollcommand=vsb.set)
        self.tradinglist_table.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        vsb.grid(row=0, column=1, sticky="ns", pady=4)

        # ── bottom row: console + graph ───────────────────────────────────────
        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="nsew", padx=6, pady=(3, 6))
        bottom.columnconfigure(0, weight=7)
        bottom.columnconfigure(1, weight=3)
        bottom.rowconfigure(0, weight=1)

        # Console (left)
        console_frame = ctk.CTkFrame(bottom, fg_color=C["bg2"], corner_radius=8)
        console_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        console_frame.columnconfigure(0, weight=1)
        console_frame.rowconfigure(1, weight=1)

        ctk.CTkLabel(console_frame, text="Console Output",
                     font=CTkFont("Segoe UI", 11, "bold"),
                     text_color=C["accent"]).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 0))

        self.console_output = ctk.CTkTextbox(
            console_frame,
            font=CTkFont("Consolas", 10),
            fg_color=C["bg1"],
            text_color=C["text"],
            scrollbar_button_color=C["border"],
            wrap="word",
            state="normal",
        )
        self.console_output.grid(row=1, column=0, sticky="nsew", padx=6, pady=(4, 6))

        # Trade Graph (right)
        graph_frame = ctk.CTkFrame(bottom, fg_color=C["bg2"], corner_radius=8)
        graph_frame.grid(row=0, column=1, sticky="nsew")
        graph_frame.columnconfigure(0, weight=1)
        graph_frame.rowconfigure(1, weight=1)

        ctk.CTkLabel(graph_frame, text="Active Trade",
                     font=CTkFont("Segoe UI", 11, "bold"),
                     text_color=C["accent"]).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 0))

        self.fig = Figure(figsize=(3, 1.8), dpi=100)
        self.ax  = self.fig.add_subplot(111)
        self.ax.axis("off")
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))


    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Tab 2: Log Files
    # ╚══════════════════════════════════════════════════════════════════════════
    def _build_log_files_tab(self):
        tab = self.notebook.tab("Log Files")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=3)
        tab.rowconfigure(1, weight=1)

        # Log viewer
        log_frame = ctk.CTkFrame(tab, fg_color=C["bg2"], corner_radius=8)
        log_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 3))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        ctk.CTkLabel(log_frame, text="Log File Output",
                     font=CTkFont("Segoe UI", 11, "bold"),
                     text_color=C["accent"]).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 0))
        self.log_text = ctk.CTkTextbox(
            log_frame,
            font=CTkFont("Consolas", 10),
            fg_color=C["bg1"], text_color=C["text"],
            scrollbar_button_color=C["border"],
            wrap="word", state="normal",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=6, pady=(4, 6))

        # Skip trade report
        skip_frame = ctk.CTkFrame(tab, fg_color=C["bg2"], corner_radius=8)
        skip_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(3, 6))
        skip_frame.columnconfigure(0, weight=1)
        skip_frame.rowconfigure(1, weight=1)
        ctk.CTkLabel(skip_frame, text="Skip Trade Report (Today)",
                     font=CTkFont("Segoe UI", 11, "bold"),
                     text_color=C["warning"]).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 0))
        self.skip_trade_report_text = ctk.CTkTextbox(
            skip_frame,
            font=CTkFont("Consolas", 10),
            fg_color=C["bg1"], text_color=C["text_dim"],
            scrollbar_button_color=C["border"],
            wrap="word", height=80, state="normal",
        )
        self.skip_trade_report_text.grid(row=1, column=0, sticky="nsew", padx=6, pady=(4, 6))


    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Tab 3: P&L and Trade Analysis
    # ╚══════════════════════════════════════════════════════════════════════════
    def _build_pnl_tab(self):
        tab = self.notebook.tab("P&L and Trade Analysis")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        card = ctk.CTkFrame(tab, fg_color=C["bg2"], corner_radius=8)
        card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        ctk.CTkLabel(card, text="P&L and Trade Analysis",
                     font=CTkFont("Segoe UI", 12, "bold"),
                     text_color=C["accent"]).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))

        cols = ("Date/Time", "Counter", "Pair", "Quantity", "P&L", "P&L %")
        self.trade_analysis_table = ttk.Treeview(
            card, columns=cols, show="headings",
            style="Dark.Treeview", height=20,
        )
        col_widths = {"Date/Time": 140, "Counter": 70, "Pair": 110,
                      "Quantity": 110, "P&L": 110, "P&L %": 100}
        for col in cols:
            self.trade_analysis_table.heading(col, text=col)
            self.trade_analysis_table.column(col, width=col_widths.get(col, 100), anchor="e" if col not in ("Date/Time", "Pair") else "w")

        self.trade_analysis_table.tag_configure("positive_pnl", foreground=C["success"])
        self.trade_analysis_table.tag_configure("negative_pnl", foreground=C["danger"])

        vsb2 = ttk.Scrollbar(card, orient="vertical",
                              command=self.trade_analysis_table.yview,
                              style="Dark.Vertical.TScrollbar")
        self.trade_analysis_table.configure(yscrollcommand=vsb2.set)
        self.trade_analysis_table.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb2.grid(row=1, column=1, sticky="ns", pady=6)


    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Bottom bar
    # ╚══════════════════════════════════════════════════════════════════════════
    def _build_bottom_bar(self):
        # pack(side="bottom") BEFORE the notebook so it always reserves its space
        bar = ctk.CTkFrame(self.root, fg_color=C["bg1"], corner_radius=0)
        bar.pack(side="bottom", fill="x")

        BTN = dict(
            font=CTkFont("Segoe UI", 11, "bold"),
            height=36, corner_radius=8,
            fg_color=C["bg3"], hover_color=C["bg4"],
            text_color=C["text"],
        )

        self.schedule_button = CompatButton(
            bar, text="▶  Run Bot", width=120,
            fg_color=C["success"], hover_color="#04a07a",
            font=BTN["font"], height=BTN["height"],
            corner_radius=BTN["corner_radius"],
            command=start_scheduled_routine,
        )
        self.schedule_button.pack(side="left", padx=(8, 4), pady=8)

        self.edit_config_button = CompatButton(
            bar, text="⚙  Config", width=100, **BTN,
            command=self.open_config_popup,
        )
        self.edit_config_button.pack(side="left", padx=4, pady=8)

        self.close_button = CompatButton(
            bar, text="✖  Close Trades", width=130,
            fg_color=C["danger"], hover_color="#b5101e",
            font=BTN["font"], height=BTN["height"],
            corner_radius=BTN["corner_radius"],
            command=self.close_trade,
        )
        self.close_button.pack(side="left", padx=4, pady=8)

        self.quit_button = CompatButton(
            bar, text="⏹  Quit", width=90, **BTN,
            command=self.exit_bot,
        )
        self.quit_button.pack(side="left", padx=4, pady=8)

        self.avoid_pair = CompatButton(
            bar, text="⛔  Blacklist", width=110, **BTN,
            command=self.open_blacklist_popup,
        )
        self.avoid_pair.pack(side="left", padx=4, pady=8)

        # ── status light (right-aligned) ──────────────────────────────────────
        status_area = ctk.CTkFrame(bar, fg_color="transparent")
        status_area.pack(side="right", padx=16, pady=8)

        ctk.CTkLabel(status_area, text="Status:",
                     font=CTkFont("Segoe UI", 10),
                     text_color=C["text_dim"]).pack(side="left", padx=(0, 4))

        self.status_canvas = tk.Canvas(
            status_area, width=20, height=20,
            bg=C["bg1"], highlightthickness=0,
        )
        self.status_canvas.pack(side="left")
        self.status_light = self.status_canvas.create_oval(3, 3, 17, 17, fill=C["danger"], outline="")

        # ── timer + trade number (right-aligned, next to status) ──────────────
        timer_area = ctk.CTkFrame(bar, fg_color="transparent")
        timer_area.pack(side="right", padx=8, pady=4)

        self.trade_timer_label = ctk.CTkLabel(
            timer_area, text="Trade Running Timer: 0 minute 0 second",
            font=CTkFont("Segoe UI", 10), text_color=C["text_dim"],
        )
        self.trade_timer_label.pack(anchor="e")

        self.trade_number_label = ctk.CTkLabel(
            timer_area, text="Trade Number: 0",
            font=CTkFont("Segoe UI", 10), text_color=C["text_dim"],
        )
        self.trade_number_label.pack(anchor="e")


    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Tab 4: Backtest
    # ╚══════════════════════════════════════════════════════════════════════════
    def _build_backtest_tab(self):
        tab = self.notebook.tab("Backtest")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=0)   # controls
        tab.rowconfigure(1, weight=0)   # progress bar
        tab.rowconfigure(2, weight=1)   # results

        # ── controls row ──────────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(tab, fg_color=C["bg2"], corner_radius=8)
        ctrl.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))

        ctk.CTkLabel(ctrl, text="⚡  Backtest",
                     font=CTkFont("Segoe UI", 13, "bold"),
                     text_color=C["accent"]).pack(side="left", padx=12, pady=8)

        ctk.CTkLabel(ctrl, text="Days:", font=CTkFont("Segoe UI", 10),
                     text_color=C["text_dim"]).pack(side="left", padx=(12, 4))
        self._bt_days = ctk.CTkEntry(ctrl, width=55, font=CTkFont("Consolas", 11),
                                      fg_color=C["bg3"], border_color=C["border"])
        self._bt_days.insert(0, "3")
        self._bt_days.pack(side="left", pady=8)

        ctk.CTkLabel(ctrl, text="Pairs (blank = top 20 by volume):",
                     font=CTkFont("Segoe UI", 10),
                     text_color=C["text_dim"]).pack(side="left", padx=(14, 4))
        self._bt_pairs = ctk.CTkEntry(ctrl, width=280, font=CTkFont("Consolas", 11),
                                       placeholder_text="BTCUSDT, ETHUSDT, …",
                                       fg_color=C["bg3"], border_color=C["border"])
        self._bt_pairs.pack(side="left", pady=8)

        self._bt_run_btn = CompatButton(
            ctrl, text="▶  Run", width=110,
            fg_color=C["success"], hover_color="#04a07a",
            font=CTkFont("Segoe UI", 11, "bold"), height=34, corner_radius=8,
            command=self._run_backtest,
        )
        self._bt_run_btn.pack(side="left", padx=10, pady=8)

        self._bt_export_btn = CompatButton(
            ctrl, text="📥  Export CSV", width=130,
            fg_color=C["bg4"], hover_color=C["border"],
            font=CTkFont("Segoe UI", 10), height=34, corner_radius=8,
            command=self._export_backtest_csv,
            state="disabled",
        )
        self._bt_export_btn.pack(side="left", padx=4, pady=8)

        # ── progress bar ──────────────────────────────────────────────────────
        prog_frame = ctk.CTkFrame(tab, fg_color=C["bg3"], corner_radius=6, height=28)
        prog_frame.grid(row=1, column=0, sticky="ew", padx=6, pady=2)
        prog_frame.grid_propagate(False)
        prog_frame.columnconfigure(1, weight=1)

        self._bt_status = ctk.CTkLabel(
            prog_frame, text="Configure a backtest above and click Run.",
            font=CTkFont("Segoe UI", 10), text_color=C["text_muted"],
        )
        self._bt_status.grid(row=0, column=0, padx=10, pady=4, sticky="w")

        self._bt_progress = ctk.CTkProgressBar(
            prog_frame, height=8, corner_radius=4,
            fg_color=C["bg4"], progress_color=C["accent"],
        )
        self._bt_progress.set(0)
        self._bt_progress.grid(row=0, column=1, padx=(0, 10), pady=8, sticky="ew")

        # ── inner sub-tabs ────────────────────────────────────────────────────
        self._bt_inner = ctk.CTkTabview(
            tab,
            fg_color=C["bg1"],
            segmented_button_fg_color=C["bg0"],
            segmented_button_selected_color=C["accent"],
            segmented_button_unselected_color=C["bg0"],
            text_color=C["text"],
            corner_radius=8,
        )
        self._bt_inner.grid(row=2, column=0, sticky="nsew", padx=6, pady=(2, 6))
        self._bt_inner.add("📊  Summary")
        self._bt_inner.add("📋  Trades")
        self._bt_inner.add("🏆  Per Pair")

        self._build_bt_summary_tab()
        self._build_bt_trades_tab()
        self._build_bt_perpair_tab()

        # internal summary cache for CSV export
        self._bt_last_summary = None

    # ── sub-tab: Summary ──────────────────────────────────────────────────────
    def _build_bt_summary_tab(self):
        tab = self._bt_inner.tab("📊  Summary")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=0)   # KPI cards
        tab.rowconfigure(1, weight=1)   # equity chart

        # KPI cards
        kpi_frame = ctk.CTkFrame(tab, fg_color=C["bg3"], corner_radius=8)
        kpi_frame.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))

        self._bt_metric_labels = {}
        kpis = [
            ("Trades",        C["accent"]),
            ("Win Rate",      C["success"]),
            ("Total P&L",     C["success"]),
            ("Avg P&L",       C["text_dim"]),
            ("Profit Factor", C["warning"]),
            ("Max Drawdown",  C["danger"]),
            ("Sharpe Ratio",  C["accent"]),
            ("Avg Duration",  C["text_dim"]),
            ("W / L / T",     C["text_dim"]),
        ]
        for label, color in kpis:
            card = ctk.CTkFrame(kpi_frame, fg_color=C["bg4"], corner_radius=8)
            card.pack(side="left", padx=5, pady=8, ipadx=10, ipady=6)
            ctk.CTkLabel(card, text=label, font=CTkFont("Segoe UI", 9),
                         text_color=C["text_muted"]).pack()
            val_lbl = ctk.CTkLabel(card, text="—",
                                   font=CTkFont("Segoe UI", 14, "bold"),
                                   text_color=color)
            val_lbl.pack()
            self._bt_metric_labels[label] = val_lbl

        # Equity curve chart
        chart_frame = ctk.CTkFrame(tab, fg_color=C["bg2"], corner_radius=8)
        chart_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)

        self._bt_fig   = Figure(figsize=(8, 3), dpi=96)
        self._bt_ax    = self._bt_fig.add_subplot(111)
        self._bt_canvas = FigureCanvasTkAgg(self._bt_fig, master=chart_frame)
        self._bt_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew",
                                              padx=4, pady=4)
        self._draw_empty_chart()

    def _draw_empty_chart(self):
        self._bt_ax.clear()
        self._bt_ax.set_facecolor(C["bg1"])
        self._bt_fig.patch.set_facecolor(C["bg2"])
        self._bt_ax.text(0.5, 0.5, "Run a backtest to see the equity curve",
                          transform=self._bt_ax.transAxes,
                          ha="center", va="center",
                          fontsize=11, color=C["text_muted"])
        self._bt_ax.set_xticks([])
        self._bt_ax.set_yticks([])
        for spine in self._bt_ax.spines.values():
            spine.set_color(C["border"])
        self._bt_canvas.draw()

    # ── sub-tab: Trades ───────────────────────────────────────────────────────
    def _build_bt_trades_tab(self):
        tab = self._bt_inner.tab("📋  Trades")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        cols = ("Signal Time", "Pair", "Entry", "SL", "TP",
                "R:R", "Result", "Net P&L %", "Duration")
        self._bt_table = ttk.Treeview(
            tab, columns=cols, show="headings",
            style="Dark.Treeview", height=25,
        )
        col_w = {"Signal Time": 130, "Pair": 90, "Entry": 100,
                 "SL": 100, "TP": 100, "R:R": 55,
                 "Result": 75, "Net P&L %": 85, "Duration": 80}
        for c in cols:
            self._bt_table.heading(c, text=c,
                                    command=lambda col=c: self._bt_sort_table(col))
            self._bt_table.column(
                c, width=col_w.get(c, 90),
                anchor="w" if c in ("Signal Time", "Pair") else "e",
            )
        self._bt_table.tag_configure("win",     foreground=C["success"])
        self._bt_table.tag_configure("loss",    foreground=C["danger"])
        self._bt_table.tag_configure("timeout", foreground=C["warning"])

        vsb = ttk.Scrollbar(tab, orient="vertical",
                            command=self._bt_table.yview,
                            style="Dark.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(tab, orient="horizontal",
                            command=self._bt_table.xview,
                            style="Dark.Horizontal.TScrollbar")
        self._bt_table.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._bt_table.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns", pady=6)
        hsb.grid(row=1, column=0, sticky="ew", padx=(6, 0))

        self._bt_sort_col   = None
        self._bt_sort_asc   = True

    def _bt_sort_table(self, col):
        """Click-to-sort column header."""
        try:
            rows = [(self._bt_table.set(r, col), r)
                    for r in self._bt_table.get_children()]
            try:
                rows.sort(key=lambda x: float(x[0].replace('%', '').replace('+', '')),
                           reverse=not self._bt_sort_asc)
            except ValueError:
                rows.sort(reverse=not self._bt_sort_asc)
            for i, (_, row) in enumerate(rows):
                self._bt_table.move(row, '', i)
            self._bt_sort_asc = not self._bt_sort_asc
        except Exception:
            pass

    # ── sub-tab: Per Pair ─────────────────────────────────────────────────────
    def _build_bt_perpair_tab(self):
        tab = self._bt_inner.tab("🏆  Per Pair")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        cols = ("Pair", "Trades", "Wins", "Losses",
                "Win Rate", "Total P&L %", "Avg R:R")
        self._bt_pair_table = ttk.Treeview(
            tab, columns=cols, show="headings",
            style="Dark.Treeview", height=25,
        )
        col_w = {"Pair": 120, "Trades": 70, "Wins": 60,
                 "Losses": 65, "Win Rate": 80,
                 "Total P&L %": 100, "Avg R:R": 80}
        for c in cols:
            self._bt_pair_table.heading(c, text=c)
            self._bt_pair_table.column(
                c, width=col_w.get(c, 90),
                anchor="w" if c == "Pair" else "e",
            )
        self._bt_pair_table.tag_configure("positive", foreground=C["success"])
        self._bt_pair_table.tag_configure("negative",  foreground=C["danger"])

        vsb = ttk.Scrollbar(tab, orient="vertical",
                            command=self._bt_pair_table.yview,
                            style="Dark.Vertical.TScrollbar")
        self._bt_pair_table.configure(yscrollcommand=vsb.set)
        self._bt_pair_table.grid(row=0, column=0, sticky="nsew",
                                  padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns", pady=6)

    # ── runner ────────────────────────────────────────────────────────────────
    def _run_backtest(self):
        """Launch backtest in a background thread so the UI stays responsive."""
        import threading
        try:
            from backtest.engine import BacktestEngine
        except ImportError as exc:
            messagebox.showerror("Backtest Error",
                                  f"Could not import backtest engine:\n{exc}")
            return

        days_text  = self._bt_days.get().strip()
        pairs_text = self._bt_pairs.get().strip()
        try:
            days = float(days_text) if days_text else 3.0
        except ValueError:
            days = 3.0
        pairs = [p.strip().upper() for p in pairs_text.split(",")
                 if p.strip()] or None

        self._bt_run_btn.configure(state="disabled")
        self._bt_export_btn.configure(state="disabled")
        self._bt_progress.set(0)
        self._bt_status.configure(text="Fetching pairs…", text_color=C["warning"])

        def _progress(pair, pct):
            self.root.after(0, lambda p=pair, v=pct: (
                self._bt_status.configure(text=f"Processing {p}…"),
                self._bt_progress.set(v),
            ))

        def _work():
            try:
                engine      = BacktestEngine(progress_cb=_progress)
                final_pairs = pairs or BacktestEngine.get_top_pairs(20)
                n_candles   = int(days * 24 * 60)
                summary     = engine.run(pairs=final_pairs, n_candles=n_candles)
                self.root.after(0, lambda s=summary: self._display_backtest(s))
            except Exception as exc:
                self.root.after(0, lambda e=exc: (
                    self._bt_status.configure(
                        text=f"Error: {e}", text_color=C["danger"]),
                    self._bt_progress.set(0),
                ))
            finally:
                self.root.after(0, lambda: (
                    self._bt_run_btn.configure(state="normal"),
                    self._bt_progress.set(1),
                ))

        threading.Thread(target=_work, daemon=True, name="BacktestWorker").start()

    # ── display results ───────────────────────────────────────────────────────
    def _display_backtest(self, summary):
        """Populate all three sub-tabs with results (always called on main thread)."""
        try:
            s = self._bt_last_summary = summary
            pnl_ok = s.total_pnl_pct >= 0

            # ── KPI cards ─────────────────────────────────────────────────────
            kpi_data = {
                "Trades":        (str(s.total_trades),               C["accent"]),
                "Win Rate":      (f"{s.win_rate}%",                   C["success"] if s.win_rate >= 50 else C["danger"]),
                "Total P&L":    (f"{s.total_pnl_pct:+.2f}%",         C["success"] if pnl_ok else C["danger"]),
                "Avg P&L":      (f"{s.avg_pnl_pct:+.3f}%",           C["success"] if pnl_ok else C["danger"]),
                "Profit Factor": (str(s.profit_factor),               C["success"] if s.profit_factor >= 1.2 else C["danger"]),
                "Max Drawdown":  (f"{s.max_drawdown_pct:.2f}%",       C["warning"]),
                "Sharpe Ratio":  (str(s.sharpe_ratio),                C["accent"]),
                "Avg Duration":  (f"{s.avg_duration:.0f} min",        C["text_dim"]),
                "W / L / T":     (f"{s.wins} / {s.losses} / {s.timeouts}", C["text_dim"]),
            }
            for key, (val, col) in kpi_data.items():
                if key in self._bt_metric_labels:
                    self._bt_metric_labels[key].configure(text=val, text_color=col)

            # ── equity curve ──────────────────────────────────────────────────
            self._bt_ax.clear()
            self._bt_ax.set_facecolor(C["bg1"])
            self._bt_fig.patch.set_facecolor(C["bg2"])

            if s.equity_curve:
                eq = s.equity_curve
                xs = list(range(len(eq)))

                # Fill above/below zero
                self._bt_ax.fill_between(
                    xs, eq, 0,
                    where=[v >= 0 for v in eq],
                    color=C["success"], alpha=0.25, interpolate=True,
                )
                self._bt_ax.fill_between(
                    xs, eq, 0,
                    where=[v < 0 for v in eq],
                    color=C["danger"], alpha=0.25, interpolate=True,
                )
                self._bt_ax.plot(xs, eq, color=C["accent"], linewidth=1.5)
                self._bt_ax.axhline(0, color=C["border"], linewidth=0.8,
                                     linestyle="--")

                self._bt_ax.set_xlabel("Trade #",      color=C["text_dim"], fontsize=9)
                self._bt_ax.set_ylabel("Cumul. P&L %", color=C["text_dim"], fontsize=9)
                self._bt_ax.set_title(
                    f"Equity Curve  |  {len(eq)} trades  |  "
                    f"Net P&L: {eq[-1]:+.2f}%",
                    color=C["text"], fontsize=10,
                )
                self._bt_ax.tick_params(colors=C["text_dim"], labelsize=8)

            for spine in self._bt_ax.spines.values():
                spine.set_color(C["border"])
            self._bt_fig.tight_layout(pad=1.2)
            self._bt_canvas.draw()

            # ── Trades table ──────────────────────────────────────────────────
            self._bt_table.delete(*self._bt_table.get_children())
            for t in sorted(s.trades, key=lambda x: x.signal_time, reverse=True):
                tag = t.result.lower()
                self._bt_table.insert("", "end", tags=(tag,), values=(
                    t.signal_time.strftime("%Y-%m-%d %H:%M"),
                    t.pair,
                    f"{t.entry:.5f}",
                    f"{t.stop_loss:.5f}",
                    f"{t.take_profit:.5f}",
                    f"{t.rr_ratio:.2f}",
                    t.result,
                    f"{t.pnl_net_pct:+.3f}%",
                    f"{t.duration_bars}",
                ))

            # ── Per Pair table ────────────────────────────────────────────────
            self._bt_pair_table.delete(*self._bt_pair_table.get_children())
            sorted_pairs = sorted(
                s.per_pair.items(),
                key=lambda x: x[1]["total_pnl"],
                reverse=True,
            )
            for pair, d in sorted_pairs:
                tag = "positive" if d["total_pnl"] >= 0 else "negative"
                self._bt_pair_table.insert("", "end", tags=(tag,), values=(
                    pair,
                    d["trades"],
                    d["wins"],
                    d["trades"] - d["wins"],
                    f"{d['win_rate']:.1f}%",
                    f"{d['total_pnl']:+.2f}%",
                    f"{d['avg_rr']:.2f}",
                ))

            # ── final status ──────────────────────────────────────────────────
            self._bt_status.configure(
                text=f"✅  Complete — {s.total_trades} trades in {s.run_time}s",
                text_color=C["success"],
            )
            self._bt_export_btn.configure(state="normal")

        except Exception as exc:
            self._bt_status.configure(
                text=f"Display error: {exc}", text_color=C["danger"]
            )

    # ── export ────────────────────────────────────────────────────────────────
    def _export_backtest_csv(self):
        """Save the last backtest results to a CSV file chosen by the user."""
        import csv, pathlib
        from tkinter import filedialog

        s = self._bt_last_summary
        if not s or not s.trades:
            messagebox.showinfo("Export", "No backtest results to export.")
            return

        path = filedialog.asksaveasfilename(
            title="Save Backtest Results",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="backtest_results.csv",
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # Header
                writer.writerow(["SPORTYX Backtest Export",
                                  f"Generated: {tk.datetime.now() if hasattr(tk, 'datetime') else __import__('datetime').datetime.now()}", ""])
                writer.writerow([])

                # Summary
                writer.writerow(["SUMMARY"])
                writer.writerow(["Metric", "Value"])
                for key, lbl in self._bt_metric_labels.items():
                    writer.writerow([key, lbl.cget("text")])
                writer.writerow([])

                # Trades
                writer.writerow(["TRADES"])
                writer.writerow(["Signal Time", "Pair", "Entry", "SL", "TP",
                                  "R:R", "Result", "Net P&L %", "Duration (bars)"])
                for t in sorted(s.trades, key=lambda x: x.signal_time):
                    writer.writerow([
                        t.signal_time.strftime("%Y-%m-%d %H:%M"),
                        t.pair, t.entry, t.stop_loss, t.take_profit,
                        t.rr_ratio, t.result,
                        f"{t.pnl_net_pct:+.3f}", t.duration_bars,
                    ])
                writer.writerow([])

                # Per Pair
                writer.writerow(["PER PAIR"])
                writer.writerow(["Pair", "Trades", "Wins", "Win Rate %",
                                  "Total P&L %", "Avg R:R"])
                for pair, d in sorted(s.per_pair.items(),
                                       key=lambda x: x[1]["total_pnl"], reverse=True):
                    writer.writerow([pair, d["trades"], d["wins"],
                                     d["win_rate"], d["total_pnl"], d["avg_rr"]])

            messagebox.showinfo("Export", f"Results saved to:\n{path}")

        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  is_running property  (getter + setter that updates status light)
    # ╚══════════════════════════════════════════════════════════════════════════
    @property
    def is_running(self):
        return self._is_running

    @is_running.setter
    def is_running(self, value):
        self._is_running = value
        try:
            color = C["success"] if value else C["danger"]
            self.status_canvas.itemconfig(self.status_light, fill=color)
        except Exception:
            pass


    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Public methods  (called from scheduler / place_trade / utility / main)
    # ╚══════════════════════════════════════════════════════════════════════════
    def update_trade_timer(self, seconds):
        """Update the running timer label."""
        try:
            minutes = int(seconds) // 60
            secs    = int(seconds) % 60
            self.trade_timer_label.configure(
                text=f"Trade Running Timer: {minutes} minutes {secs} seconds"
            )
        except Exception:
            pass

    def update_trade_number(self, trade_number):
        """Update the trade counter label and persist to disk."""
        global trade_counter
        try:
            self.trade_number_label.configure(text=f"Trade Number: {trade_number}")
            save_trade_counter(trade_counter)
        except Exception:
            pass

    def update_trade_details_table(self, order_reports, update_existing=False):
        """Add or update rows in the Current Trades table."""
        global placed_order_ids
        try:
            for report in order_reports:
                price = float(report['price']) if float(report['price']) != 0 else float(report.get('stopPrice', 0))
                existing_item = None

                for row in self.tradinglist_table.get_children():
                    if self.tradinglist_table.item(row, 'values')[2] == str(report['orderId']):
                        existing_item = row
                        break

                if update_existing and existing_item:
                    current = list(self.tradinglist_table.item(existing_item, 'values'))
                    current[-1] = report['status']
                    self.tradinglist_table.item(existing_item, values=current)
                else:
                    valid_time = get_valid_time(report)
                    self.tradinglist_table.insert("", "end", values=(
                        valid_time,
                        report['symbol'],
                        report['orderId'],
                        report['type'],
                        report['side'],
                        f"${float(price):.5f}",
                        f"{float(report['origQty']):.5f}",
                        report['status'],
                    ))
                    placed_order_ids[report['orderId']] = report['symbol']
        except Exception as e:
            self.add_to_console(f"[Table update error] {e}")

    def add_to_console(self, message):
        """Append a message to the console output (thread-safe)."""
        def _do():
            try:
                self.console_output.configure(state="normal")
                self.console_output.insert("end", str(message) + "\n")
                self.console_output.see("end")
            except Exception:
                pass
        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def start_trade_graph(self, entry_price, stop_loss, take_profit, pair_name):
        """Draw the SL / Entry / TP graph for an active trade."""
        try:
            self.entry_price  = entry_price
            self.stop_loss    = stop_loss
            self.take_profit  = take_profit
            self.pair_name    = pair_name

            self.ax.clear()
            self.ax.plot(
                [stop_loss, entry_price, take_profit], [0, 0, 0],
                marker='o', color=C["text"], linewidth=2,
            )
            self.ax.text(stop_loss,    -0.12, f'SL\n${stop_loss}',    color=C["danger"],  ha='center', fontsize=8)
            self.ax.text(entry_price,  -0.06, f'Entry\n${entry_price}', color=C["text"],   ha='center', fontsize=8)
            self.ax.text(take_profit,  -0.12, f'TP\n${take_profit}',  color=C["success"], ha='center', fontsize=8)

            self.ax.axvline(entry_price, color=C["text_dim"], linestyle='--', ymin=0.2, ymax=0.8)
            self.ax.text(entry_price, 0.08, f'Current (${entry_price})', color=C["text_dim"], ha='center', fontsize=7)
            self.ax.text(0.05, 0.92, pair_name, transform=self.ax.transAxes, fontsize=9, ha='left', color=C["accent"])

            self.ax.axis('off')
            self.canvas.draw()
        except Exception as e:
            self.add_to_console(f"[Graph error] {e}")

    def update_current_price(self, current_price):
        """Refresh the current-price line on the trade graph."""
        try:
            self.current_price = current_price
            self.ax.clear()

            self.ax.plot(
                [self.stop_loss, self.entry_price, self.take_profit], [0, 0, 0],
                marker='o', color=C["text"], linewidth=2,
            )
            self.ax.text(self.stop_loss,   -0.12, f'SL\n${self.stop_loss}',    color=C["danger"],  ha='center', fontsize=8)
            self.ax.text(self.entry_price, -0.06, f'Entry\n${self.entry_price}', color=C["text"],   ha='center', fontsize=8)
            self.ax.text(self.take_profit, -0.12, f'TP\n${self.take_profit}',  color=C["success"], ha='center', fontsize=8)

            self.ax.axvline(current_price, color=C["accent"], linestyle='--', ymin=0.2, ymax=0.8)
            self.ax.text(current_price, 0.08, f'Current (${current_price:.4f})', color=C["accent"], ha='center', fontsize=7)
            self.ax.text(0.05, 0.92, self.pair_name, transform=self.ax.transAxes, fontsize=9, ha='left', color=C["accent"])

            self.ax.axis('off')
            self.canvas.draw()
        except Exception:
            pass   # graph may not be initialised yet — silently ignore

    def stop_trade_graph(self):
        """Clear the trade graph."""
        try:
            self.ax.clear()
            self.ax.axis('off')
            self.canvas.draw()
        except Exception:
            pass

    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Button commands
    # ╚══════════════════════════════════════════════════════════════════════════
    def exit_bot(self):
        """Quit button handler."""
        try:
            safe_binance_call(client.close)  # close Binance session if open
        except Exception as e:
            self.add_to_console(f"Error closing session: {e}")
        global is_running, trade_counter
        is_running = False
        save_trade_counter(trade_counter)
        self.root.quit()

    def close_trade(self):
        """Close Trades button handler."""
        close_orders()
        update_trading_table()
        self.stop_trade_graph()

    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Config popup
    # ╚══════════════════════════════════════════════════════════════════════════
    def open_config_popup(self):
        """Open a scrollable popup to edit config values."""
        self.edit_config_button.configure(state="disabled")

        def save_config():
            for var, entry in entries.items():
                value = entry.get()
                try:
                    if isinstance(config[var], (int, float)):
                        config[var] = type(config[var])(value)
                    elif isinstance(config[var], tuple):
                        if all('.' in item for item in value.split(',')):
                            config[var] = tuple(map(float, value.split(',')))
                        else:
                            config[var] = tuple(map(int, value.split(',')))
                    elif isinstance(config[var], list):
                        config[var] = value.split(',')
                    else:
                        config[var] = value
                except ValueError:
                    self.add_to_console(f"Invalid input for {var}, keeping original value.")
            self.edit_config_button.configure(state="normal")
            popup.destroy()

        def reset_fields():
            for var, entry in entries.items():
                entry.delete(0, "end")
                v = config[var]
                entry.insert(0, ','.join(map(str, v)) if isinstance(v, (tuple, list)) else str(v))

        def disable_close():
            messagebox.showwarning("Action Blocked", "Use SAVE or RESET to close this window.")

        popup = ctk.CTkToplevel(self.root)
        popup.title("Edit Configuration")
        popup.geometry("480x520")
        popup.configure(fg_color=C["bg0"])
        popup.protocol("WM_DELETE_WINDOW", disable_close)
        popup.grab_set()

        scroll = ctk.CTkScrollableFrame(popup, fg_color=C["bg1"],
                                         scrollbar_button_color=C["border"])
        scroll.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        scroll.columnconfigure(1, weight=1)

        entries = {}
        for idx, (key, value) in enumerate(config.items()):
            ctk.CTkLabel(scroll, text=key, font=CTkFont("Segoe UI", 10, "bold"),
                         text_color=C["text"], anchor="w").grid(
                row=idx * 2, column=0, sticky="w", padx=8, pady=(4, 0))

            entry = ctk.CTkEntry(scroll, width=220, font=CTkFont("Consolas", 10),
                                  fg_color=C["bg3"], border_color=C["border"])
            entry.grid(row=idx * 2, column=1, sticky="ew", padx=8, pady=(4, 0))
            entries[key] = entry
            v_str = ','.join(map(str, value)) if isinstance(value, (tuple, list)) else str(value)
            entry.insert(0, v_str)

            desc = descriptions.get(key, "")
            if desc:
                ctk.CTkLabel(scroll, text=desc, font=CTkFont("Segoe UI", 9),
                             text_color=C["text_muted"], wraplength=420, anchor="w").grid(
                    row=idx * 2 + 1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2))

        btn_row = ctk.CTkFrame(popup, fg_color=C["bg0"])
        btn_row.pack(fill="x", padx=10, pady=8)

        ctk.CTkButton(btn_row, text="SAVE", command=save_config, width=100,
                      fg_color=C["success"], hover_color="#04a07a",
                      font=CTkFont("Segoe UI", 11, "bold")).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="RESET", command=reset_fields, width=100,
                      fg_color=C["bg3"], hover_color=C["bg4"],
                      font=CTkFont("Segoe UI", 11, "bold")).pack(side="left", padx=6)

    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Blacklist popup
    # ╚══════════════════════════════════════════════════════════════════════════
    def open_blacklist_popup(self):
        """Open a popup to add/remove blacklisted pairs."""
        global blacklist
        self.avoid_pair.configure(state="disabled")

        def save_blacklist_changes():
            global blacklist
            new_pair = entry_field.get().strip()
            if new_pair and new_pair not in blacklist:
                blacklist.append(new_pair)
            save_blacklist()
            _refresh_list()
            entry_field.delete(0, "end")

        def remove_selected():
            global blacklist
            sel = listbox.curselection()
            for idx in reversed(sel):
                blacklist.pop(idx)
            save_blacklist()
            _refresh_list()

        def _refresh_list():
            listbox.delete(0, "end")
            for pair in blacklist:
                listbox.insert("end", pair)

        def close_popup():
            self.avoid_pair.configure(state="normal")
            popup.destroy()

        def disable_close():
            messagebox.showwarning("Action Blocked", "Use ADD, REMOVE or CLOSE to proceed.")

        popup = ctk.CTkToplevel(self.root)
        popup.title("Edit Blacklist")
        popup.geometry("340x380")
        popup.configure(fg_color=C["bg0"])
        popup.protocol("WM_DELETE_WINDOW", disable_close)
        popup.grab_set()

        frame = ctk.CTkFrame(popup, fg_color=C["bg1"], corner_radius=8)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame, text="Blacklisted Pairs",
                     font=CTkFont("Segoe UI", 11, "bold"),
                     text_color=C["warning"]).pack(pady=(8, 4))

        # tk.Listbox styled for dark theme
        lb_frame = ctk.CTkFrame(frame, fg_color=C["bg2"], corner_radius=6)
        lb_frame.pack(fill="both", expand=True, padx=8, pady=4)

        listbox = tk.Listbox(lb_frame, selectmode="multiple", height=10,
                              bg=C["bg2"], fg=C["text"],
                              selectbackground=C["accent"],
                              font=("Consolas", 10),
                              relief="flat", highlightthickness=0,
                              activestyle="none")
        listbox.pack(fill="both", expand=True, padx=4, pady=4)

        entry_field = ctk.CTkEntry(frame, placeholder_text="BTCUSDT",
                                    fg_color=C["bg3"], border_color=C["border"],
                                    font=CTkFont("Consolas", 11))
        entry_field.pack(fill="x", padx=8, pady=(4, 0))

        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=8)

        ctk.CTkButton(btn_row, text="ADD", width=90,
                      fg_color=C["success"], hover_color="#04a07a",
                      command=save_blacklist_changes).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="REMOVE", width=90,
                      fg_color=C["danger"], hover_color="#b5101e",
                      command=remove_selected).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="CLOSE", width=90,
                      fg_color=C["bg3"], hover_color=C["bg4"],
                      command=close_popup).pack(side="left", padx=4)

        _refresh_list()

    # ╔══════════════════════════════════════════════════════════════════════════
    # ║  Log / analysis updates (called from tab-change hook)
    # ╚══════════════════════════════════════════════════════════════════════════
    def update_log_tab(self):
        """Refresh main log file view and skip trade report."""
        try:
            with open(self.log_file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", content)
            self.log_text.see("end")
        except Exception as e:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"Error reading log file: {e}\n")
            self.log_text.see("end")

        self.generate_skip_trade_report()

    def generate_skip_trade_report(self):
        """Parse skip log and show today's summary."""
        reasons = {}
        try:
            with open(self.skip_trade_log_path, "r", encoding="utf-8", errors="replace") as f:
                today = datetime.now().date()
                for line in f:
                    if "Skipping" in line and "%" in line:
                        try:
                            date_str = line.split("%")[0].strip().rstrip(' -')
                            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').date()
                            if dt == today:
                                reason = line.split("%")[1].strip()
                                reasons[reason] = reasons.get(reason, 0) + 1
                        except ValueError:
                            pass

            self.skip_trade_report_text.configure(state="normal")
            self.skip_trade_report_text.delete("1.0", "end")
            self.skip_trade_report_text.insert("end", "Today's Skip Trade Summary:\n\n")
            for reason, count in reasons.items():
                desc = skip_reason_descriptions.get(reason, "No description available.")
                self.skip_trade_report_text.insert(
                    "end", f"  {reason}: {count} occurrence(s)\n    → {desc}\n\n"
                )
        except Exception as e:
            self.skip_trade_report_text.configure(state="normal")
            self.skip_trade_report_text.insert("end", f"Error: {e}\n")

    def update_trade_analysis_tab(self):
        """Populate the P&L table from trade_logs.log."""
        self.trade_analysis_table.delete(*self.trade_analysis_table.get_children())
        trades = defaultdict(lambda: {"BUY": None, "SELL": None})
        try:
            with open(self.trade_log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(
                        r".*Trade closed - \[(.*?)\] - (-?\d+) - (.*?) - (BUY|SELL) - (.*?) - (.*?) - (.*?) - (.*?)$",
                        line
                    )
                    if m:
                        timestamp, tc, pair, action, quantity, price, pnl, _ = m.groups()
                        tc = int(tc)
                        trades[tc][action] = {
                            "pair": pair,
                            "price": float(price),
                            "quantity": abs(float(quantity)),
                            "pnl": float(pnl),
                            "time": timestamp,
                        }

            for tc, actions in trades.items():
                if actions["BUY"] and actions["SELL"]:
                    sell_qty     = actions["SELL"]["quantity"]
                    pnl_pct      = ((actions["SELL"]["price"] - abs(actions["BUY"]["price"]))
                                    / abs(actions["BUY"]["price"])) * 100
                    pnl          = round((actions["SELL"]["price"] * sell_qty) +
                                         (actions["BUY"]["price"] * sell_qty * -1), 5)
                    tag = "positive_pnl" if pnl >= 0 else "negative_pnl"
                    self.trade_analysis_table.insert("", "end",
                        values=(actions["SELL"]["time"], tc, actions["SELL"]["pair"],
                                sell_qty, pnl, f"{round(pnl_pct, 2)} %"),
                        tags=(tag,)
                    )
        except Exception as e:
            self.trade_analysis_table.insert("", "end", values=("Error", str(e), "", "", "", ""))

    def on_tab_change(self, event):
        """Legacy hook kept for compatibility (not used with CTkTabview)."""
        pass

    def _on_ctk_tab_change(self):
        """Called by CTkTabview when the user switches tabs.
        Note: this version of CTkTabview calls command() with no arguments,
        so we read the active tab via self.notebook.get()."""
        try:
            tab_name = self.notebook.get()
            if tab_name == "Log Files":
                self.update_log_tab()
            elif tab_name == "P&L and Trade Analysis":
                self.update_trade_analysis_tab()
        except Exception:
            pass


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Module-level helpers  (same as original UI.py)
# ╚══════════════════════════════════════════════════════════════════════════════
def get_valid_time(report):
    """Return the most recent valid timestamp from an order report."""
    transact_time = report.get('transactTime')
    order_time    = report.get('time')
    time_value    = (max(transact_time, order_time)
                     if transact_time and order_time
                     else transact_time or order_time)
    return (datetime.fromtimestamp(time_value / 1000).strftime('%Y-%m-%d %H:%M')
            if time_value else "N/A")


def stop_scheduled_routine(gui):
    """Stop the background scheduler thread."""
    global active_thread
    gui.add_to_console("Stopping the scheduled routine...")
    gui.is_running = False
    if active_thread and active_thread.is_alive():
        active_thread.join(timeout=5)
        if active_thread.is_alive():
            gui.add_to_console("Warning: Routine thread did not terminate properly.")
        else:
            gui.add_to_console("Scheduled routine successfully stopped.")
    active_thread = None


def start_scheduled_routine():
    """Start the scheduler thread (called by the Run Bot button)."""
    gui = get_gui_instance()
    global active_thread

    stop_scheduled_routine(gui)

    gui.schedule_button.configure(state="disabled")
    gui.is_running = True
    active_thread = threading.Thread(target=scheduled_routine, daemon=True)
    active_thread.start()
    gui.add_to_console("Scheduled BOT started. Checking trades every minute.")
