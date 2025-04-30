
from collections import defaultdict
from datetime import datetime
import re
import threading
from misc.config import *
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
import tkinter.messagebox as messagebox
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import tkinter as tk
import time
from trade.force_close import close_orders
from scheduler import scheduled_routine
from core.shared_state import get_gui_instance
from utilities.utility import save_blacklist, save_trade_counter, update_trading_table

class CryptoBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SPORTYX Crypto Bot")
        self.root.iconbitmap(icon_file)
        
        # Log file paths
        self.log_file_path = f'{script_dir}\\logs\\trading_bot.log'
        self.trade_log_path = f'{script_dir}\\logs\\trade_logs.log'
        self.detailed_trade_log_path = f'{script_dir}\\logs\\detailed_trade_logs.log'
        self.skip_trade_log_path = f'{script_dir}\\logs\\skip_trade_logs.log'

        # Configure grid layout for responsiveness
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=5)  # Top table gets 60% (3 out of 5)
        self.root.rowconfigure(1, weight=2)  # Bottom row gets 40% (2 out of 5)
        self.root.rowconfigure(2, weight=0, minsize=50)  # 50px height (adjust as needed)

        # Notebook (Tabbed Interface)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(column=0, row=0, columnspan=2, sticky="nsew")  # Use grid for the notebook

        # Tab 1: Current Trades
        self.current_trades_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.current_trades_frame, text="Current Trades")        

        # Add components to Tab 1 (Current Trades)
        self.tradinglist_table = ttk.Treeview(
            self.current_trades_frame,
            columns=("Date/Time", "Pair", "Order ID", "Type", "Side", "Price", "Quantity", "Status"),
            show="headings",
            height=10
        )
        self.tradinglist_table.heading("Pair", text="Pair", anchor="w")
        self.tradinglist_table.heading("Date/Time", text="Date/Time", anchor="w")        
        self.tradinglist_table.heading("Order ID", text="Order ID", anchor="w")
        self.tradinglist_table.heading("Type", text="Type", anchor="w")
        self.tradinglist_table.heading("Side", text="Side", anchor="w")
        self.tradinglist_table.heading("Price", text="Price", anchor="w")
        self.tradinglist_table.heading("Quantity", text="Quantity", anchor="w")
        self.tradinglist_table.heading("Status", text="Status", anchor="w")
        self.tradinglist_table.column("Pair", width=50, anchor="w")
        self.tradinglist_table.column("Date/Time", width=50, anchor="w")        
        self.tradinglist_table.column("Order ID", width=50, anchor="w")
        self.tradinglist_table.column("Type", width=50, anchor="w")
        self.tradinglist_table.column("Side", width=50, anchor="w")
        self.tradinglist_table.column("Price", width=50, anchor="w")
        self.tradinglist_table.column("Quantity", width=50, anchor="w")
        self.tradinglist_table.column("Status", width=50, anchor="w")
        
        self.tradinglist_table.grid(column=0, row=0, columnspan=2, sticky="nsew")
        self.current_trades_frame.columnconfigure(0, weight=1)
        self.current_trades_frame.rowconfigure(0, weight=1)

        # Console Output Frame (Bottom Left - 70%)
        self.console_frame = ttk.LabelFrame(self.current_trades_frame, text="Console Output", padding="10")
        self.console_frame.grid(column=0, row=1, sticky="nsew", padx=5, pady=5)
        
        self.console_frame.columnconfigure(0, weight=1)
        self.console_frame.rowconfigure(0, weight=1)

        self.console_output = ScrolledText(self.console_frame, wrap=tk.WORD, height=10)
        self.console_output.pack(fill="both", expand=True)

        # Current Trade Frame (Bottom Right - 30%)
        self.current_trade_frame = ttk.LabelFrame(self.current_trades_frame, text="Current Trade", padding="10")
        self.current_trade_frame.grid(column=1, row=1, sticky="nsew", padx=5, pady=5)
        
        # Configure current_trade_frame
        self.current_trade_frame.columnconfigure(0, weight=1)
        self.current_trade_frame.rowconfigure(0, weight=1)

        # Create placeholder for the graph
        self.fig = Figure(figsize=(3, 1), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.current_trade_frame)
        self.ax.axis('off')  # Hide axes and grid
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True) 
        
        # Tab 2: Log Files
        self.log_files_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.log_files_frame, text="Log Files")
        
        # Create a LabelFrame for log content
        self.log_label_frame = ttk.LabelFrame(self.log_files_frame, text="Log File Output", padding="10")
        self.log_label_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Add ScrolledText to the LabelFrame
        self.log_text = ScrolledText(self.log_label_frame, wrap=tk.WORD, height=20)
        self.log_text.pack(fill="both", expand=True)

        # Bind tab selection event to update log when the Log Files tab is selected
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_change)
        
        # Tab 3: P&L and Trade Analysis
        self.trade_analysis_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.trade_analysis_frame, text="P&L and Trade Analysis")
        
        # Add a section for the skip trade analysis report
        self.skip_trade_report_frame = ttk.LabelFrame(self.log_files_frame, text="Skip Trade Report", padding="10")
        self.skip_trade_report_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.skip_trade_report_text = ScrolledText(self.skip_trade_report_frame, wrap=tk.WORD, height=10)
        self.skip_trade_report_text.pack(fill="both", expand=True)

        # Add P&L and Trade Analysis Content
        self.trade_analysis_table = ttk.Treeview(
            self.trade_analysis_frame,
            columns=("Date/Time", "Counter", "Pair", "Quantity", "P&L", "P&L %"),
            show="headings",
            height=15
        )
        self.trade_analysis_table.heading("Date/Time", text="Date/Time")
        self.trade_analysis_table.heading("Counter", text="Counter")
        self.trade_analysis_table.heading("Pair", text="Pair")
        self.trade_analysis_table.heading("Quantity", text="Quantity")
        self.trade_analysis_table.heading("P&L", text="P&L")
        self.trade_analysis_table.heading("P&L %", text="P&L %")
        self.trade_analysis_table.column("Date/Time", width=120, anchor="w")
        self.trade_analysis_table.column("Counter", width=50, anchor="e")
        self.trade_analysis_table.column("Pair", width=100, anchor="c")
        self.trade_analysis_table.column("Quantity", width=100, anchor="e")
        self.trade_analysis_table.column("P&L", width=100, anchor="e")
        self.trade_analysis_table.column("P&L %", width=100, anchor="e")
        # Define tag styles for positive and negative P&L
        self.trade_analysis_table.tag_configure("positive_pnl", foreground="black")
        self.trade_analysis_table.tag_configure("negative_pnl", foreground="red")
        self.trade_analysis_table.pack(fill="none", expand=True, anchor="nw")

        # Button Section
        self.button_frame = ttk.Frame(self.root)
        self.button_frame.grid(column=0, row=2, columnspan=2, pady=5, sticky="nsew")
        
        # Configure current_trade_frame
        self.button_frame.columnconfigure(0, weight=1)

        self.schedule_button = ttk.Button(self.button_frame, text="Run Bot Schedule", command=start_scheduled_routine)
        self.schedule_button.pack(side=tk.LEFT, padx=5)

        self.edit_config_button = ttk.Button(self.button_frame, text="Edit Config", command=self.open_config_popup)
        self.edit_config_button.pack(side=tk.LEFT, padx=5)

        self.close_button = ttk.Button(self.button_frame, text="Close Trades", command=self.close_trade)
        self.close_button.pack(side=tk.LEFT, padx=5)

        self.quit_button = ttk.Button(self.button_frame, text="Quit", command=self.exit_bot)
        self.quit_button.pack(side=tk.LEFT, padx=5)
        
        self.avoid_pair = ttk.Button(self.button_frame, text="Add/Remove Bad Pair", command=self.open_blacklist_popup)
        self.avoid_pair.pack(side=tk.LEFT, padx=5)
    

        # Timer and Trade Number Section
        self.timer_frame = ttk.Frame(self.button_frame)
        self.timer_frame.pack(side=tk.RIGHT, padx=10)

        self.trade_timer_label = ttk.Label(self.timer_frame, text="Trade Running Timer: 0 minute 0 second")
        self.trade_timer_label.pack(anchor="e")
        
        self.trade_number_label = ttk.Label(self.timer_frame, text="Trade Number: 0")
        self.trade_number_label.pack(anchor="e")
        
        # Add status light to the button section
        self.status_frame = ttk.Frame(self.button_frame)
        self.status_frame.pack(side=tk.RIGHT, padx=50)

        self.status_label = ttk.Label(self.status_frame, text="Status:")
        self.status_label.pack(side=tk.LEFT)

        self.status_canvas = tk.Canvas(self.status_frame, width=20, height=20)
        self.status_canvas.pack(side=tk.LEFT)
        self.status_light = self.status_canvas.create_oval(5, 5, 15, 15, fill="red")  # Initial status is red

    @property
    def is_running(self):
        """
        Getter for the is_running property.
        """
        return self._is_running

    @is_running.setter
    def is_running(self, value):
        """
        Setter for the is_running property.
        Updates the status light when the value changes.
        """
        self._is_running = value
        color = "green" if value else "red"
        self.status_canvas.itemconfig(self.status_light, fill=color)
        #print(f"Status changed to: {'Running' if value else 'Stopped'}")
        
    def update_trade_timer(self, seconds):
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        self.trade_timer_label.config(
            text=f"Trade Running Timer: {minutes} minutes {remaining_seconds} seconds"
        )
        
    def update_trade_number(self, trade_number):
        global trade_counter
        self.trade_number_label.config(text=f"Trade Number: {trade_number}")    
        save_trade_counter(trade_counter)        
        
    def exit_bot(self):
        try:
            client.session.close()
        except Exception as e:
            self.console_output.insert(tk.END, f"\nError closing session: {e}")
        global is_running
        global trade_counter
        is_running = False
        save_trade_counter(trade_counter)
        self.root.quit()  # Properly invoke the quit method
        
    def close_trade(self):  
                
        close_orders() # Properly invoke the quit method  
        update_trading_table()
        self.stop_trade_graph()  
        
    
    def open_config_popup(self):
        """Opens a popup window to edit config values."""
        self.edit_config_button.config(state=tk.DISABLED)
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
                    print(f"Invalid input for {var}, keeping the original value.")
            self.edit_config_button.config(state=tk.ACTIVE)
            popup.destroy()
            

        def reset_fields():
            for var, entry in entries.items():
                if isinstance(config[var], (tuple, list)):
                    entry.delete(0, tk.END)
                    entry.insert(0, ','.join(map(str, config[var])))
                else:
                    entry.delete(0, tk.END)
                    entry.insert(0, str(config[var]))

        def disable_close():
            messagebox.showwarning(
            "Action Blocked",
            "Close button disabled. Use 'SAVE' or 'RESET' to proceed."
        )


        popup = tk.Toplevel()
        popup.title("Edit Configuration")

        # Disable the close button (X)
        popup.protocol("WM_DELETE_WINDOW", disable_close)

        # Scrollable container
        canvas = tk.Canvas(popup, width=400, height=400)
        scroll_y = ttk.Scrollbar(popup, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll_y.set)

        # Layout for scrollable area
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")

        entries = {}
        for idx, (key, value) in enumerate(config.items()):
            ttk.Label(scrollable_frame, text=key).grid(column=0, row=idx * 2, sticky=tk.W, padx=5, pady=2)
            entry = ttk.Entry(scrollable_frame, width=30)
            entry.grid(column=1, row=idx * 2, padx=5, pady=2)
            entries[key] = entry

            if isinstance(value, (tuple, list)):
                entry.insert(0, ','.join(map(str, value)))
            else:
                entry.insert(0, str(value))

            ttk.Label(scrollable_frame, text=descriptions.get(key, "")).grid(column=0, row=idx * 2 + 1, columnspan=2, sticky=tk.W, padx=5, pady=2)

        # Buttons
        button_frame = ttk.Frame(popup, padding="10")
        button_frame.grid(column=0, row=len(config) * 2, sticky=(tk.W, tk.E))

        save_button = ttk.Button(button_frame, text="SAVE", command=save_config)
        save_button.pack(side=tk.LEFT, padx=5)

        reset_button = ttk.Button(button_frame, text="RESET", command=reset_fields)
        reset_button.pack(side=tk.LEFT, padx=5)    
    
    def update_trade_details_table(self, order_reports, update_existing=False):
        global placed_order_ids  # Use the global list to track unfilled/closed order IDs
        #gui.add_to_console(f"Updating table with: {order_reports}")
        """Update the table with order details, updating existing rows if necessary."""
        for report in order_reports:
            #gui.add_to_console(f"Updating table with: {report}")
            price = float(report['price']) if float(report['price']) != 0 else float(report['stopPrice'])
            existing_item = None

            # Check for existing rows based on orderId
            for row in self.tradinglist_table.get_children():
                row_values = self.tradinglist_table.item(row, 'values')
                #self.add_to_console(f"row value and orderId: {row_values[2]} - {report['orderId']}")
                if row_values[2] == str(report['orderId']):  # Match orderId
                    existing_item = row
                    break

            #if existing_item:
            #    self.add_to_console(f"Found existing row for orderId: {report['orderId']}")
            #else:
            #    self.add_to_console(f"No existing row found for orderId: {report['orderId']}")
                
            if update_existing and existing_item:                
                # Get current values of the existing row
                current_values = self.tradinglist_table.item(existing_item, 'values')

                # Update only the 'status' field (last field in the current_values tuple)
                updated_values = list(current_values)
                #self.add_to_console(f"Amending record:\n  {updated_values} \n {report['status']}")
                updated_values[-1] = report['status']  # Assuming 'status' is the last field

                # Update the row with the modified values
                self.tradinglist_table.item(existing_item, values=updated_values)
            else:
                # Add new row     
                
                # Use helper function to determine the valid time
                valid_time = get_valid_time(report)
                                           
                self.tradinglist_table.insert("", "end", values=(
                    valid_time,
                    report['symbol'],                    
                    report['orderId'],
                    report['type'],
                    report['side'],
                    f"${float(price):.5f}",
                    f"{float(report['origQty']):.5f}",
                    report['status']                    
                ))                
                placed_order_ids[report['orderId']] = report['symbol']

    def add_to_console(self, message):
        self.console_output.insert(tk.END, message + "\n")  # Add new message
        self.console_output.see(tk.END)  # Auto-scroll to the end
        
    def start_trade_graph(self, entry_price, stop_loss, take_profit, pair_name):
        """
        Initialize the trade graph when a new trade is executed.
        Args:
            entry_price (float): Entry price of the trade.
            stop_loss (float): Stop-loss price.
            take_profit (float): Take-profit price.
            pair_name (str): The trading pair (e.g., BTC/USDT).
        """
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.pair_name = pair_name

        # Clear the graph
        self.ax.clear()

        # Plot SL, Entry, TP
        self.ax.plot(
            [self.stop_loss, self.entry_price, self.take_profit],
            [0, 0, 0],
            marker='o',
            color='black'
        )
        self.ax.text(self.stop_loss, -0.05, f'SL \n (${self.stop_loss})', color='red', ha='center')  # Move up by 0.2
        self.ax.text(self.entry_price, -0.02, f'Entry (${self.entry_price})', color='black', ha='center')  # Move up
        self.ax.text(self.take_profit, -0.05, f'TP \n (${self.take_profit})', color='green', ha='center')  # Move up

        # Add initial Current Price line
        self.ax.axvline(self.entry_price, color='gray', linestyle='--', label=f'Current (${self.entry_price})', ymin=0.8, ymax=0.2)
        self.ax.text(self.entry_price, 0.01, f'Current (${self.entry_price})', color='gray', ha='center')  # Move up

        # Add the pair name in the top-left corner
        self.ax.text(0.05, 0.9, self.pair_name, transform=self.ax.transAxes, fontsize=10, ha='left', color='blue')

        # Hide axes and grid
        self.ax.axis('off')

        # Redraw the canvas
        self.canvas.draw()

    def update_current_price(self, current_price):
        """
        Update the current price dynamically in the graph.
        Args:
            current_price (float): The latest current price of the asset.
        """
        self.current_price = current_price  # Update the current price

        # Clear and re-plot the graph
        self.ax.clear()

        # Re-plot SL, Entry, TP
        self.ax.plot(
            [self.stop_loss, self.entry_price, self.take_profit],
            [0, 0, 0],
            marker='o',
            color='black'
        )
        self.ax.text(self.stop_loss, -0.05, f'SL \n (${self.stop_loss})', color='red', ha='center')  # Move up by 0.2
        self.ax.text(self.entry_price, -0.02, f'Entry (${self.entry_price})', color='black', ha='center')  # Move up
        self.ax.text(self.take_profit, -0.05, f'TP \n (${self.take_profit})', color='green', ha='center')  # Move up

        # Update Current Price line
        self.ax.axvline(self.current_price, color='gray', linestyle='--', label=f'Current (${self.current_price:.4f})', ymin=0.8, ymax=0.2)
        self.ax.text(self.current_price, 0.01, f'Current (${self.current_price:.4f})', color='gray', ha='center')  # Move up

        # Add the pair name in the top-left corner
        self.ax.text(0.05, 0.9, self.pair_name, transform=self.ax.transAxes, fontsize=10, ha='left', color='blue')

        # Hide axes and grid
        self.ax.axis('off')

        # Redraw the canvas
        self.canvas.draw()

    def stop_trade_graph(self):
        """
        Stop updating the graph and clear the trade status.
        """
        self.ax.clear()
        # Hide axes and grid
        self.ax.axis('off')
        self.canvas.draw()
        
    def open_blacklist_popup(self):
        global blacklist
        """Open a popup window to edit the blacklist."""
        self.avoid_pair.config(state=tk.DISABLED)
        def save_blacklist_changes():
            global blacklist
            new_pair = blacklist_text.get().strip()
            if new_pair and new_pair not in blacklist:  # Avoid adding duplicates or empty entries
                blacklist.append(new_pair)
            save_blacklist()
            update_listbox()
            blacklist_text.delete(0, tk.END)  # Clear the entry field

        def remove_selected_pair():
            global blacklist
            selected = blacklist_listbox.curselection()
            if selected:
                for index in selected[::-1]:  # Remove in reverse order to avoid index shift
                    blacklist.pop(index)
                save_blacklist()
                update_listbox()

        def update_listbox():
            global blacklist
            blacklist_listbox.delete(0, tk.END)
            for pair in blacklist:
                blacklist_listbox.insert(tk.END, pair)
                
        def close_blacklist_popup():
            self.avoid_pair.config(state=tk.ACTIVE)
            popup.destroy()

        def disable_close():
            messagebox.showwarning(
            "Action Blocked",
            "Close button disabled. Use 'SAVE' or 'CLOSE' to proceed."
        )


        popup = tk.Toplevel()
        popup.title("Edit Blacklist")

        # Disable the close button (X)
        popup.protocol("WM_DELETE_WINDOW", disable_close)
        

        frame = ttk.Frame(popup, padding="10")
        frame.grid(column=0, row=0, sticky="nsew")

        blacklist_listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, height=10, width=40)
        blacklist_listbox.grid(column=0, row=0, columnspan=3, pady=5)

        blacklist_text = ttk.Entry(frame, width=30)
        blacklist_text.grid(column=0, row=1, columnspan=3, pady=5)        

        save_button = ttk.Button(frame, text="ADD", command=save_blacklist_changes)
        save_button.grid(column=0, row=2, padx=5, pady=5)

        remove_button = ttk.Button(frame, text="REMOVE", command=remove_selected_pair)
        remove_button.grid(column=1, row=2, padx=5, pady=5)

        close_button = ttk.Button(frame, text="CLOSE", command=close_blacklist_popup)
        close_button.grid(column=2, row=2, padx=5, pady=5)
        
        update_listbox()

    def update_log_tab(self):
        """
        Read the log file and update the log tab.
        """
        try:
            with open(self.log_file_path, "r") as log_file:
                content = log_file.read()
                self.log_text.delete("1.0", tk.END)  # Clear current content
                self.log_text.insert(tk.END, content)  # Insert updated content
                self.log_text.see(tk.END)  # Scroll to the end
        except Exception as e:
            self.log_text.insert(tk.END, f"Error reading log file: {e}\n")
            self.log_text.see(tk.END)
            
        # Update skip trade report
        self.generate_skip_trade_report()

    def on_tab_change(self, event):
        """
        Triggered when a tab is selected. Only update log tab when Log Files is selected.
        """
        current_tab = self.notebook.index(self.notebook.select())
        if current_tab == 1:  # If the Log Files tab (index 1) is selected
            self.update_log_tab()
        elif current_tab == 2:  # P&L and Trade Analysis tab
            self.update_trade_analysis_tab()
            
    def generate_skip_trade_report(self):
        """
        Analyze skip_trade_logger and provide a summary of skip reasons.
        """
        reasons = {}
        try:
            with open(self.skip_trade_log_path, "r") as skip_log:
                today_date = datetime.now().date()                        
                for line in skip_log:
                    if "Skipping" in line and "%" in line:
                        date_and_time = line.split("%")[0].strip().rstrip(' -')        
                        date_and_time = datetime.strptime(date_and_time, '%Y-%m-%d %H:%M:%S').date()        
                        if date_and_time == today_date:
                            reason = line.split("%")[1].strip()
                            reasons[reason] = reasons.get(reason, 0) + 1

            # Display the skip trade report
            self.skip_trade_report_text.delete("1.0", tk.END)
            self.skip_trade_report_text.insert(tk.END, "Today Skip Trade Summary:\n\n")
            for reason, count in reasons.items():
                description = skip_reason_descriptions.get(reason, "No description available.")
                self.skip_trade_report_text.insert(
                tk.END, f"{reason}: {count} occurrences\n  - {description}\n\n"
            )
        except Exception as e:
            self.skip_trade_report_text.insert(tk.END, f"Error generating skip trade report: {e}\n")

    def update_trade_analysis_tab(self):
        """
        Populate the P&L and trade analysis table with data from trade_logger.
        """
        self.trade_analysis_table.delete(*self.trade_analysis_table.get_children())  # Clear the table
        trades = defaultdict(lambda: {"BUY": None, "SELL": None})
        try:
            with open(self.trade_log_path, "r") as trade_log:
                for line in trade_log:
                    # Match trade log format
                    match = re.match(r".*Trade closed - \[(.*?)\] - (-?\d+) - (.*?) - (BUY|SELL) - (.*?) - (.*?) - (.*?) - (.*?)$", line)
                    if match:
                        timestamp, trade_counter, pair, action, quantity, price, pnl, _ = match.groups()
                        trade_counter = int(trade_counter)
                        trades[trade_counter][action] = {                            
                            "pair": pair,
                            "price": float(price),
                            "quantity": abs(float(quantity)),  # Ensure positive quantity
                            "pnl": float(pnl),
                            "time": timestamp
                        }
                
                # Calculate P&L for each trade counter
                for trade_counter, actions in trades.items():
                    if actions["BUY"] and actions["SELL"]:
                        sell_quantity = actions["SELL"]["quantity"]  # Use SELL quantity for calculations
                        purchase_cost = sell_quantity * actions["BUY"]["price"]
                        revenue = sell_quantity * actions["SELL"]["price"]
                        pnl_perc = ((actions["SELL"]["price"]-abs(actions["BUY"]["price"]))/ abs(actions["BUY"]["price"])) * 100 # Convert to percentage
                        pnl_perc = str(round(pnl_perc,2)) + " %"                        
                        pnl = revenue + purchase_cost   
                        pnl = round(pnl, 5)
                        date_time = actions["SELL"]["time"]    
                        pair = actions["SELL"]["pair"]            
                        
                        # Determine the tag based on P&L value
                        tag = "positive_pnl" if pnl >= 0 else "negative_pnl"

                        # Insert the row with the appropriate tag
                        self.trade_analysis_table.insert("", "end", values=(date_time, trade_counter, pair, sell_quantity, pnl, pnl_perc), tags=(tag,))                

        except Exception as e:
            self.trade_analysis_table.insert("", "end", values=("Error", str(e), "", "", ""))
            
def get_valid_time(report):
    """
    Determine the valid time from `transactTime` and `time` in a report.
    Prioritize the later of the two timestamps.
    """
    transact_time = report.get('transactTime')
    order_time = report.get('time')
    
    time_value = max(transact_time, order_time) if transact_time and order_time else transact_time or order_time

    # Safeguard against missing timestamps
    formatted_time = (
        datetime.fromtimestamp(time_value / 1000).strftime('%Y-%m-%d %H:%M')
        if time_value else "N/A"
    )
    
    return formatted_time
          
    
# Start the routine in a separate thread
def stop_scheduled_routine(gui):
    """
    Stop the currently running scheduled routine thread.
    """    
    global active_thread
    gui.add_to_console("Stopping the scheduled routine...")
    
    # Signal the routine to stop
    gui.is_running = False

    if active_thread and active_thread.is_alive():
        active_thread.join(timeout=5)  # Wait for it to terminate
        if active_thread.is_alive():
            gui.add_to_console("Warning: Routine thread did not terminate properly.")
        else:
            gui.add_to_console("Scheduled routine successfully stopped.")

    active_thread = None  # Reset thread tracking


def start_scheduled_routine():
    """
    Start the scheduled routine, ensuring only one thread runs at a time.
    """
    
    gui = get_gui_instance()
    global active_thread

    # Ensure no duplicate routines run
    stop_scheduled_routine(gui)  

    gui.schedule_button.config(state=tk.DISABLED)

    gui.is_running = True
    active_thread = threading.Thread(target=scheduled_routine, daemon=True)
    active_thread.start()

    gui.add_to_console("Scheduled BOT started. Checking trades every minute.")