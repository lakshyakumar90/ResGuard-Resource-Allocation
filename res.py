import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import time
import json
import os
import pandas as pd
import dash
from dash import dcc, html
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from flask import Flask
import webbrowser
import numpy as np
import psutil  # Added psutil for real system monitoring

if not os.environ.get("DISPLAY"):
    print("No display found, running in CLI mode.")
    # Run CLI-based logic here instead of GUI
    exit()

# Global Variables
mutex = threading.Lock()
# Instead of fixed resources, we'll dynamically get them
resources = {
    "CPU": 0,  # Will be updated with real CPU availability
    "Memory": 0,  # Will be updated with real memory availability in GB
    "Disk": 0,  # Will be updated with real disk space in GB
    "Network": 0  # Will be updated with network bandwidth estimation
}
users = {"admin": "admin123", "User1": "pass1", "User2": "pass2", "User3": "pass3"}
allocation = {user: {res: 0 for res in resources} for user in users}
max_claim = {user: {res: 0 for res in resources} for user in users}  # Max resources each user might claim
logs = []
dashboard_server_running = False

# Ensure data directory exists
os.makedirs("data", exist_ok=True)
STATE_FILE = "data/resource_state.json"
LOG_FILE = "data/resource_logs.json"
MAX_CLAIM_FILE = "data/max_claim.json"

# Function to update system resources using psutil
def update_system_resources():
    # CPU availability (number of logical cores - load)
    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count(logical=True)
    resources["CPU"] = max(0.1, cpu_count * (100 - cpu_percent) / 100)  # Scaled to represent available CPU power
    
    # Memory availability in GB
    memory = psutil.virtual_memory()
    resources["Memory"] = round(memory.available / (1024 ** 3), 2)  # Available memory in GB
    
    # Disk space in GB (using root path)
    disk = psutil.disk_usage('/')
    resources["Disk"] = round(disk.free / (1024 ** 3), 2)  # Free disk space in GB
    
    # Network bandwidth estimation (simplified)
    # This is a rough approximation, as actual bandwidth measurement requires more complex tracking
    net_io = psutil.net_io_counters()
    resources["Network"] = round(100 - (net_io.bytes_sent + net_io.bytes_recv) % 100, 2)  # Simplified network availability
    
    return resources

# Load previous state
def load_state():
    global allocation, logs, max_claim
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                allocation = data.get("allocation", allocation)
        
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                logs = json.load(f)
        
        if os.path.exists(MAX_CLAIM_FILE):
            with open(MAX_CLAIM_FILE, "r") as f:
                max_claim = json.load(f)
        else:
            # Initialize max claims with reasonable defaults based on available resources
            update_system_resources()  # Get current system resources
            for user in users:
                for res in resources:
                    # Set max claim to 70% of total resource by default
                    total_resource = resources[res] + sum(allocation[u][res] for u in users)
                    max_claim[user][res] = total_resource * 0.7
    except Exception as e:
        print(f"Error loading state: {e}")
        # Continue with default values if there's an error

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"allocation": allocation}, f, indent=2)
        
        with open(LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2)
        
        with open(MAX_CLAIM_FILE, "w") as f:
            json.dump(max_claim, f, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}")
        messagebox.showerror("Save Error", f"Failed to save state: {e}")

def log_event(event):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} - {event}"
    logs.append(log_entry)
    
    # Update the log display if it exists
    if 'log_display' in globals() and log_display.winfo_exists():
        log_display.configure(state='normal')
        log_display.insert(tk.END, log_entry + "\n")
        log_display.see(tk.END)
        log_display.configure(state='disabled')
    
    # Save after each log to prevent data loss
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

def check_resource_usage():
    update_system_resources()  # Get latest resource values
    alerts = []
    for res, value in resources.items():
        total_allocated = sum(allocation[user][res] for user in users)
        total_available = value
        usage_percentage = (total_allocated / (total_allocated + total_available)) * 100 if (total_allocated + total_available) > 0 else 0
        
        if usage_percentage > 80:
            alerts.append(f"{res} usage is high ({usage_percentage:.1f}%)")
        
        if res == "CPU" and value < 0.5:
            alerts.append(f"{res} availability is critically low!")
        elif res == "Memory" and value < 1.0:
            alerts.append(f"{res} availability is critically low!")
        elif res == "Disk" and value < 5.0:
            alerts.append(f"{res} availability is critically low!")
        elif res == "Network" and value < 10.0:
            alerts.append(f"{res} availability is critically low!")
    
    return alerts

# ----------------- BANKER'S ALGORITHM IMPLEMENTATION -----------------

def is_safe_state():
    """
    Implements the Banker's Algorithm to check if the system is in a safe state.
    Returns a tuple: (is_safe, safe_sequence or None)
    """
    update_system_resources()  # Get latest resource values
    
    # Create working copies of the resource vectors
    work = {res: resources[res] for res in resources}
    finish = {user: False for user in users}
    
    # Need vector: maximum needs - current allocation
    need = {}
    for user in users:
        need[user] = {}
        for res in resources:
            need[user][res] = max_claim[user][res] - allocation[user][res]
    
    # Find a safe sequence
    safe_sequence = []
    
    while True:
        found = False
        for user in users:
            if not finish[user]:
                # Check if all resource needs can be met
                can_allocate = True
                for res in resources:
                    if need[user][res] > work[res]:
                        can_allocate = False
                        break
                
                if can_allocate:
                    # This user's needs can be satisfied
                    for res in resources:
                        work[res] += allocation[user][res]
                    finish[user] = True
                    safe_sequence.append(user)
                    found = True
        
        if not found:
            break
    
    # Check if all processes are finished
    is_safe = all(finish.values())
    
    return (is_safe, safe_sequence if is_safe else None)

def request_resource(user, resource):
    with mutex:
        update_system_resources()  # Get latest resource values
        
        # Check if the resource is available
        if resources[resource] <= 0:
            messagebox.showwarning("Resource Unavailable", f"No {resource} units available!")
            return False
        
        # Calculate how much to allocate (scaled for different resource types)
        allocation_amount = 0.1  # Default small amount
        if resource == "CPU":
            allocation_amount = 0.1  # CPU cores
        elif resource == "Memory":
            allocation_amount = 0.5  # 0.5 GB of RAM
        elif resource == "Disk":
            allocation_amount = 1.0  # 1 GB of disk
        elif resource == "Network":
            allocation_amount = 5.0  # 5 units of network bandwidth
        
        # Check if resource has enough to allocate
        if resources[resource] < allocation_amount:
            messagebox.showwarning("Resource Unavailable", 
                f"Not enough {resource} available! (Needed: {allocation_amount}, Available: {resources[resource]:.2f})")
            return False
        
        # Check if this request would exceed max claim
        if allocation[user][resource] + allocation_amount > max_claim[user][resource]:
            messagebox.showwarning("Claim Exceeded", 
                f"Request denied: Would exceed your maximum claim of {max_claim[user][resource]:.2f} for {resource}!")
            return False
        
        # Temporarily allocate the resource to check safety
        allocation[user][resource] += allocation_amount
        
        # Check if this allocation leads to a safe state
        is_safe, safe_sequence = is_safe_state()
        
        if is_safe:
            log_event(f"{user} allocated {allocation_amount} unit of {resource} - System is in safe state")
            log_event(f"Safe sequence: {' -> '.join(safe_sequence)}")
            
            # Check for alerts after allocation
            alerts = check_resource_usage()
            if alerts:
                messagebox.showwarning("Resource Alert", "\n".join(alerts))
            
            save_state()
            return True
        else:
            # Revert the allocation as it would lead to an unsafe state
            allocation[user][resource] -= allocation_amount
            log_event(f"{user} denied {allocation_amount} unit of {resource} - Would lead to unsafe state")
            messagebox.showwarning("Unsafe Allocation", 
                "Request denied: Granting this resource would lead to a potential deadlock!")
            return False

def release_resource(user, resource):
    with mutex:
        # Calculate how much to release (match request amounts)
        release_amount = 0.1  # Default small amount
        if resource == "CPU":
            release_amount = 0.1  # CPU cores
        elif resource == "Memory":
            release_amount = 0.5  # 0.5 GB of RAM
        elif resource == "Disk":
            release_amount = 1.0  # 1 GB of disk
        elif resource == "Network":
            release_amount = 5.0  # 5 units of network bandwidth
        
        if allocation[user][resource] >= release_amount:
            allocation[user][resource] -= release_amount
            log_event(f"{user} released {release_amount} unit of {resource}")
            # After releasing, system is always in a safer state
            is_safe, safe_sequence = is_safe_state()
            if is_safe:
                log_event(f"System is in safe state after release. Safe sequence: {' -> '.join(safe_sequence)}")
            save_state()
            return True
        else:
            messagebox.showwarning("Invalid Release", 
                f"{user} has not allocated enough {resource}! (Has: {allocation[user][resource]:.2f}, Trying to release: {release_amount})")
            return False

def update_max_claim(user, resource, new_max):
    with mutex:
        # Validate the new maximum claim
        if new_max < allocation[user][resource]:
            messagebox.showwarning("Invalid Max Claim", 
                f"Maximum claim cannot be less than current allocation ({allocation[user][resource]:.2f})!")
            return False
        
        # Update the max claim
        max_claim[user][resource] = new_max
        log_event(f"{user}'s maximum claim for {resource} updated to {new_max}")
        
        # Check if the system is still in a safe state
        is_safe, safe_sequence = is_safe_state()
        if is_safe:
            log_event(f"System remains in safe state after max claim update. Safe sequence: {' -> '.join(safe_sequence)}")
            save_state()
            return True
        else:
            # This shouldn't happen from just updating max claim if current allocation is valid
            # but we'll check anyway
            log_event(f"Warning: System is now in unsafe state after max claim update!")
            messagebox.showwarning("Safety Warning", 
                "The system is now in an unsafe state! Consider releasing some resources.")
            save_state()
            return True  # Still allow the update, but warn the user

def system_monitor():
    """Thread function to continuously monitor system resources"""
    while True:
        try:
            time.sleep(5)  # Update every 5 seconds
            with mutex:
                old_resources = resources.copy()
                update_system_resources()
                
                # Log significant changes
                for res in resources:
                    if abs(resources[res] - old_resources[res]) > old_resources[res] * 0.1:  # 10% change
                        log_event(f"System {res} changed from {old_resources[res]:.2f} to {resources[res]:.2f}")
                
                # Check if system is still in safe state after resource changes
                is_safe, safe_sequence = is_safe_state()
                if not is_safe:
                    log_event("WARNING: System entered unsafe state due to resource changes!")
        except Exception as e:
            print(f"Error in system_monitor thread: {e}")

# -------------------- DASH REAL-TIME DASHBOARD --------------------

server = Flask(__name__)
app = dash.Dash(__name__, server=server)

# Dashboard Layout
app.layout = html.Div(children=[
    html.H1("ResGuard Resource Monitoring Dashboard", style={'textAlign': 'center'}),
    
    html.Div([
        html.Div([
            html.H3("Available Resources", style={'textAlign': 'center'}),
            dcc.Graph(id="resources-graph"),
        ], className='six columns'),
        
        html.Div([
            html.H3("Resource Allocation by User", style={'textAlign': 'center'}),
            dcc.Graph(id="allocation-graph"),
        ], className='six columns'),
    ], className='row'),
    
    html.Div([
        html.H3("System Resource Usage", style={'textAlign': 'center'}),
        dcc.Graph(id="system-usage-graph"),
    ]),
    
    html.Div([
        html.H3("Banker's Algorithm Safety Status", style={'textAlign': 'center'}),
        html.Div(id="safety-status", style={
            'border': '1px solid #ddd',
            'padding': '10px',
            'marginBottom': '20px',
            'textAlign': 'center',
            'fontSize': '18px'
        })
    ]),
    
    html.Div([
        html.H3("System Logs", style={'textAlign': 'center'}),
        html.Div(id="logs-container", style={
            'height': '200px',
            'overflowY': 'scroll',
            'border': '1px solid #ddd',
            'padding': '10px',
            'marginBottom': '20px'
        })
    ]),
    
    dcc.Interval(id="interval-update", interval=2000, n_intervals=0)
], style={'padding': '20px'})

# Define callback to update graphs
@app.callback(
    [Output("resources-graph", "figure"),
     Output("allocation-graph", "figure"),
     Output("system-usage-graph", "figure"),
     Output("safety-status", "children"),
     Output("logs-container", "children")],
    [Input("interval-update", "n_intervals")]
)
def update_graphs(n):
    # Update system resources
    update_system_resources()
    
    # Resources graph
    resource_data = []
    for res in resources:
        resource_data.append({"Resource": res, "Available": resources[res]})
    
    df_resources = pd.DataFrame(resource_data)
    
    fig_resources = go.Figure()
    fig_resources.add_trace(go.Bar(
        x=df_resources["Resource"],
        y=df_resources["Available"],
        marker_color='rgb(55, 83, 109)',
        name="Available"
    ))
    
    # Calculate total allocated for each resource
    for res in resources:
        total_allocated = sum(allocation[user][res] for user in users)
        df_resources.loc[df_resources["Resource"] == res, "Total"] = total_allocated + resources[res]
    
    fig_resources.add_trace(go.Bar(
        x=df_resources["Resource"],
        y=df_resources["Total"] - df_resources["Available"],
        marker_color='rgb(219, 64, 82)',
        name="Used"
    ))
    
    fig_resources.update_layout(
        barmode='stack',
        title="Resource Availability",
        xaxis_title="Resource",
        yaxis_title="Units",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    # Allocation by user graph
    user_data = []
    for user in users:
        for res in resources:
            if allocation[user][res] > 0:
                user_data.append({
                    "User": user,
                    "Resource": res,
                    "Allocated": allocation[user][res]
                })
    
    df_allocation = pd.DataFrame(user_data) if user_data else pd.DataFrame(columns=["User", "Resource", "Allocated"])
    
    fig_allocation = go.Figure()
    if not df_allocation.empty:
        for res in resources:
            df_res = df_allocation[df_allocation["Resource"] == res]
            if not df_res.empty:
                fig_allocation.add_trace(go.Bar(
                    x=df_res["User"],
                    y=df_res["Allocated"],
                    name=res
                ))
        
        fig_allocation.update_layout(
            barmode='group',
            title="Resource Allocation by User",
            xaxis_title="User",
            yaxis_title="Allocated Units",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
    else:
        fig_allocation.update_layout(
            title="No resources currently allocated",
            xaxis_title="User",
            yaxis_title="Allocated Units"
        )
    
    # System Usage Graph (new)
    system_data = []
    # CPU usage
    cpu_percent = psutil.cpu_percent()
    system_data.append({"Metric": "CPU Usage", "Value": cpu_percent})
    
    # Memory usage
    memory = psutil.virtual_memory()
    system_data.append({"Metric": "Memory Usage", "Value": memory.percent})
    
    # Disk usage
    disk = psutil.disk_usage('/')
    system_data.append({"Metric": "Disk Usage", "Value": disk.percent})
    
    # Network usage (simplified)
    system_data.append({"Metric": "Network Load", "Value": (100 - resources["Network"]) if "Network" in resources else 50})
    
    df_system = pd.DataFrame(system_data)
    
    fig_system = go.Figure()
    fig_system.add_trace(go.Bar(
        x=df_system["Metric"],
        y=df_system["Value"],
        marker_color=['blue' if x < 70 else 'orange' if x < 90 else 'red' for x in df_system["Value"]],
    ))
    
    fig_system.update_layout(
        title="System Resource Usage (%)",
        xaxis_title="Resource",
        yaxis_title="Usage (%)",
        yaxis=dict(range=[0, 100])
    )
    
    # Banker's Algorithm Safety Status
    is_safe, safe_sequence = is_safe_state()
    if is_safe:
        safety_status = [
            html.Div("✅ System is in a SAFE state", style={'color': 'green', 'fontWeight': 'bold'}),
            html.Div(f"Safe Sequence: {' → '.join(safe_sequence)}")
        ]
    else:
        safety_status = [
            html.Div("⚠️ System is in an UNSAFE state", style={'color': 'red', 'fontWeight': 'bold'}),
            html.Div("Warning: Potential deadlock condition! Consider releasing resources.")
        ]
    
    # System logs
    log_items = [html.P(log) for log in logs[-10:]]
    
    return fig_resources, fig_allocation, fig_system, safety_status, log_items

# Function to start the dashboard in a separate thread
def start_dashboard():
    global dashboard_server_running
    if not dashboard_server_running:
        dashboard_server_running = True
        # Open browser after a short delay to ensure the server is running
        threading.Timer(1.5, lambda: webbrowser.open_new("http://127.0.0.1:8050")).start()
        app.run_server(debug=False, use_reloader=False, port=8050)

# Define on_close function that was missing in the original code
def on_close(root):
    save_state()
    root.destroy()

# -------------------- TKINTER GUI --------------------

def create_gui():
    global log_display
    
    root = tk.Tk()
    root.title("Cloud Resource Manager with Real-time Monitoring")
    root.geometry("800x600")
    root.protocol("WM_DELETE_WINDOW", lambda: on_close(root))
    
    # Create a style
    style = ttk.Style()
    style.configure("TFrame", background="#f0f0f0")
    style.configure("TLabel", background="#f0f0f0", font=("Arial", 10))
    style.configure("TButton", font=("Arial", 10))
    style.configure("Safe.TLabel", foreground="green", font=("Arial", 10, "bold"))
    style.configure("Unsafe.TLabel", foreground="red", font=("Arial", 10, "bold"))
    
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)
    
    # Login frame
    login_frame = ttk.Frame(notebook, padding=20)
    notebook.add(login_frame, text="Login")
    
    # Login form
    ttk.Label(login_frame, text="Username:").grid(row=0, column=0, sticky="w", pady=5)
    user_entry = ttk.Entry(login_frame, width=30)
    user_entry.grid(row=0, column=1, sticky="w", pady=5)
    
    ttk.Label(login_frame, text="Password:").grid(row=1, column=0, sticky="w", pady=5)
    pass_entry = ttk.Entry(login_frame, show="*", width=30)
    pass_entry.grid(row=1, column=1, sticky="w", pady=5)
    
    login_status = ttk.Label(login_frame, text="")
    login_status.grid(row=2, column=0, columnspan=2, pady=10)
    
    def authenticate():
        user = user_entry.get()
        password = pass_entry.get()
        if user in users and users[user] == password:
            user_var.set(user)
            login_status.config(text=f"Logged in as {user}", foreground="green")
            notebook.select(1)  # Switch to resource management tab
            log_event(f"User {user} logged in")
        else:
            login_status.config(text="Invalid username or password!", foreground="red")
    
    ttk.Button(login_frame, text="Login", command=authenticate).grid(row=3, column=0, columnspan=2, pady=10)
    
    # Resource Management frame
    resource_frame = ttk.Frame(notebook, padding=20)
    notebook.add(resource_frame, text="Resource Management")
    
    user_var = tk.StringVar(value="")
    resource_var = tk.StringVar(value="CPU")
    
    # User and resource selection
    ttk.Label(resource_frame, text="Current User:").grid(row=0, column=0, sticky="w", pady=5)
    ttk.Label(resource_frame, textvariable=user_var).grid(row=0, column=1, sticky="w", pady=5)
    
    ttk.Label(resource_frame, text="Select Resource:").grid(row=1, column=0, sticky="w", pady=5)
    resource_menu = ttk.Combobox(resource_frame, textvariable=resource_var, values=list(resources.keys()), state="readonly")
    resource_menu.grid(row=1, column=1, sticky="w", pady=5)
    
    # Resource allocation buttons
    request_button = ttk.Button(
        resource_frame, 
        text="Request Resource", 
        command=lambda: on_request_resource(user_var.get(), resource_var.get(), status_text)
    )
    request_button.grid(row=2, column=0, pady=10, padx=5)
    
    release_button = ttk.Button(
        resource_frame, 
        text="Release Resource", 
        command=lambda: on_release_resource(user_var.get(), resource_var.get(), status_text)
    )
    release_button.grid(row=2, column=1, pady=10, padx=5)
    
    # System monitor display
    ttk.Label(resource_frame, text="System Monitor:", font=("Arial", 11, "bold")).grid(row=3, column=0, sticky="w", pady=5)
    system_frame = ttk.Frame(resource_frame)
    system_frame.grid(row=4, column=0, columnspan=2, sticky="w", pady=5)
    
    # Create labels for real-time system info
    cpu_label = ttk.Label(system_frame, text="CPU: Loading...")
    cpu_label.pack(anchor="w")
    
    memory_label = ttk.Label(system_frame, text="Memory: Loading...")
    memory_label.pack(anchor="w")
    
    disk_label = ttk.Label(system_frame, text="Disk: Loading...")
    disk_label.pack(anchor="w")
    
    network_label = ttk.Label(system_frame, text="Network: Loading...")
    network_label.pack(anchor="w")
    
    # Banker's Algorithm Status
    ttk.Label(resource_frame, text="Banker's Algorithm Status:").grid(row=5, column=0, sticky="w", pady=5)
    safety_status_var = tk.StringVar(value="Checking system safety...")
    safety_status_label = ttk.Label(resource_frame, textvariable=safety_status_var)
    safety_status_label.grid(row=5, column=1, sticky="w", pady=5)
    
    safe_sequence_var = tk.StringVar(value="")
    ttk.Label(resource_frame, textvariable=safe_sequence_var, wraplength=400).grid(row=6, column=0, columnspan=2, sticky="w", pady=5)
    
    # Resource status display
    ttk.Label(resource_frame, text="Resource Status:").grid(row=7, column=0, sticky="w", pady=5)
    status_frame = ttk.Frame(resource_frame)
    status_frame.grid(row=8, column=0, columnspan=2, sticky="w", pady=5)
    
    status_text = tk.StringVar(value="")
    ttk.Label(status_frame, textvariable=status_text, wraplength=400).pack(anchor="w")
    
    # User allocation display
    ttk.Label(resource_frame, text="Your Allocations:").grid(row=9, column=0, sticky="w", pady=5)
    user_allocation_text = tk.StringVar(value="")
    ttk.Label(resource_frame, textvariable=user_allocation_text, wraplength=400).grid(row=10, column=0, columnspan=2, sticky="w", pady=5)
    
    # Maximum claim management
    ttk.Separator(resource_frame, orient='horizontal').grid(row=11, column=0, columnspan=2, sticky="ew", pady=10)
    
    ttk.Label(resource_frame, text="Max Claim Management:").grid(row=12, column=0, sticky="w", pady=5)
    
    max_claim_frame = ttk.Frame(resource_frame)
    max_claim_frame.grid(row=13, column=0, columnspan=2, sticky="w", pady=5)
    
    ttk.Label(max_claim_frame, text="Resource:").grid(row=0, column=0, padx=5)
    max_claim_resource = ttk.Combobox(max_claim_frame, values=list(resources.keys()), state="readonly", width=10)
    max_claim_resource.grid(row=0, column=1, padx=5)
    max_claim_resource.current(0)
    
    ttk.Label(max_claim_frame, text="New Max:").grid(row=0, column=2, padx=5)
    max_claim_value = ttk.Entry(max_claim_frame, width=8)
    max_claim_value.grid(row=0, column=3, padx=5)
    
    def update_max_claim_gui():
        user = user_var.get()
        if not user:
            messagebox.showwarning("Authentication Required", "Please login first!")
            notebook.select(0)  # Switch to login tab
            return
        
        resource = max_claim_resource.get()
        if not resource:
            messagebox.showwarning("Input Error", "Please select a resource!")
            return
        
        try:
            new_max = float(max_claim_value.get())
            if new_max < 0:
                raise ValueError("Max claim must be a positive number")
                
            if update_max_claim(user, resource, new_max):
                messagebox.showinfo("Max Claim Updated", f"Maximum claim for {resource} updated to {new_max}")
        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid value: {e}")
    
    ttk.Button(max_claim_frame, text="Update Max Claim", command=update_max_claim_gui).grid(row=0, column=4, padx=5)
    
    # Dashboard button
    dashboard_button = ttk.Button(
        resource_frame, 
        text="Open Dashboard", 
        command=lambda: threading.Thread(target=start_dashboard, daemon=True).start()
    )
    dashboard_button.grid(row=14, column=0, columnspan=2, pady=20)
    
    # Logs Frame
   # Logs Frame
    logs_frame = ttk.Frame(notebook, padding=20)
    notebook.add(logs_frame, text="System Logs")
    
    ttk.Label(logs_frame, text="System Activity Logs:", font=("Arial", 11, "bold")).pack(anchor="w", pady=5)
    
    # Create a scrolled text widget for logs
    log_display = scrolledtext.ScrolledText(logs_frame, width=80, height=20)
    log_display.pack(fill="both", expand=True, padx=5, pady=5)
    log_display.configure(state='disabled')
    
    # Populate logs
    log_display.configure(state='normal')
    for log_entry in logs:
        log_display.insert(tk.END, log_entry + "\n")
    log_display.see(tk.END)
    log_display.configure(state='disabled')
    
    # Clear logs button
    def clear_logs():
        global logs
        if messagebox.askyesno("Clear Logs", "Are you sure you want to clear all logs?"):
            logs = []
            log_display.configure(state='normal')
            log_display.delete(1.0, tk.END)
            log_display.configure(state='disabled')
            save_state()
            messagebox.showinfo("Logs Cleared", "All logs have been cleared.")
    
    ttk.Button(logs_frame, text="Clear Logs", command=clear_logs).pack(pady=10)
    
    # Function to handle resource requests
    def on_request_resource(user, resource, status_var):
        if not user:
            messagebox.showwarning("Authentication Required", "Please login first!")
            notebook.select(0)  # Switch to login tab
            return
        
        if request_resource(user, resource):
            status_var.set(f"Successfully allocated {resource} to {user}")
            update_gui_info()
        else:
            status_var.set(f"Failed to allocate {resource} to {user}")
    
    # Function to handle resource releases
    def on_release_resource(user, resource, status_var):
        if not user:
            messagebox.showwarning("Authentication Required", "Please login first!")
            notebook.select(0)  # Switch to login tab
            return
        
        if release_resource(user, resource):
            status_var.set(f"Successfully released {resource} from {user}")
            update_gui_info()
        else:
            status_var.set(f"Failed to release {resource} from {user}")
    
    # Function to update GUI information
    def update_gui_info():
        # Update system resource labels
        update_system_resources()
        cpu_label.config(text=f"CPU: {resources['CPU']:.2f} units available")
        memory_label.config(text=f"Memory: {resources['Memory']:.2f} GB available")
        disk_label.config(text=f"Disk: {resources['Disk']:.2f} GB available")
        network_label.config(text=f"Network: {resources['Network']:.2f} units available")
        
        # Update safety status
        is_safe, safe_sequence = is_safe_state()
        if is_safe:
            safety_status_var.set("✅ SAFE")
            safety_status_label.config(style="Safe.TLabel")
            safe_sequence_var.set(f"Safe sequence: {' → '.join(safe_sequence)}")
        else:
            safety_status_var.set("⚠️ UNSAFE")
            safety_status_label.config(style="Unsafe.TLabel")
            safe_sequence_var.set("System is in an unsafe state! Risk of deadlock.")
        
        # Update user allocations
        user = user_var.get()
        if user:
            alloc_text = ""
            for res in resources:
                alloc_text += f"{res}: {allocation[user][res]:.2f} / {max_claim[user][res]:.2f} (used/max)\n"
            user_allocation_text.set(alloc_text)
    
    # Periodic update function
    def periodic_update():
        if root.winfo_exists():
            update_gui_info()
            root.after(2000, periodic_update)  # Update every 2 seconds
    
    # Start periodic updates
    root.after(1000, periodic_update)
    
    # Initial update
    update_gui_info()
    
    # Start system monitor in a separate thread
    monitor_thread = threading.Thread(target=system_monitor, daemon=True)
    monitor_thread.start()
    
    return root

def main():
    # Load previous state
    load_state()
    
    # Create and run the GUI
    root = create_gui()
    root.mainloop()

if __name__ == "__main__":
    main()