import threading
import time
import json
import os
import pandas as pd
import dash
from dash import dcc, html, Input, Output, State, callback
import plotly.graph_objs as go
from flask import Flask, redirect, url_for, request, session, flash
import webbrowser
import numpy as np
import psutil  # Added psutil for real system monitoring

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

def log_event(event):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} - {event}"
    logs.append(log_entry)
    
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
            log_event(f"{user} request denied: No {resource} units available!")
            return False, f"No {resource} units available!"
        
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
            message = f"Not enough {resource} available! (Needed: {allocation_amount}, Available: {resources[resource]:.2f})"
            log_event(f"{user} request denied: {message}")
            return False, message
        
        # Check if this request would exceed max claim
        if allocation[user][resource] + allocation_amount > max_claim[user][resource]:
            message = f"Request denied: Would exceed your maximum claim of {max_claim[user][resource]:.2f} for {resource}!"
            log_event(f"{user} request denied: {message}")
            return False, message
        
        # Temporarily allocate the resource to check safety
        allocation[user][resource] += allocation_amount
        
        # Check if this allocation leads to a safe state
        is_safe, safe_sequence = is_safe_state()
        
        if is_safe:
            log_event(f"{user} allocated {allocation_amount} unit of {resource} - System is in safe state")
            log_event(f"Safe sequence: {' -> '.join(safe_sequence)}")
            
            # Check for alerts after allocation
            alerts = check_resource_usage()
            alert_message = ""
            if alerts:
                alert_message = "Alerts: " + ", ".join(alerts)
                log_event(f"Resource alerts: {alert_message}")
            
            save_state()
            return True, f"Successfully allocated {allocation_amount} unit of {resource}" + (f". {alert_message}" if alert_message else "")
        else:
            # Revert the allocation as it would lead to an unsafe state
            allocation[user][resource] -= allocation_amount
            message = "Request denied: Granting this resource would lead to a potential deadlock!"
            log_event(f"{user} denied {allocation_amount} unit of {resource} - Would lead to unsafe state")
            return False, message

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
            return True, f"Successfully released {release_amount} unit of {resource}"
        else:
            message = f"{user} has not allocated enough {resource}! (Has: {allocation[user][resource]:.2f}, Trying to release: {release_amount})"
            log_event(message)
            return False, message

def update_max_claim(user, resource, new_max):
    with mutex:
        try:
            new_max = float(new_max)
            # Validate the new maximum claim
            if new_max < allocation[user][resource]:
                message = f"Maximum claim cannot be less than current allocation ({allocation[user][resource]:.2f})!"
                log_event(f"{user} max claim update failed: {message}")
                return False, message
            
            # Update the max claim
            max_claim[user][resource] = new_max
            log_event(f"{user}'s maximum claim for {resource} updated to {new_max}")
            
            # Check if the system is still in a safe state
            is_safe, safe_sequence = is_safe_state()
            if is_safe:
                log_event(f"System remains in safe state after max claim update. Safe sequence: {' -> '.join(safe_sequence)}")
                save_state()
                return True, f"Maximum claim for {resource} updated to {new_max}"
            else:
                # This shouldn't happen from just updating max claim if current allocation is valid
                # but we'll check anyway
                log_event(f"Warning: System is now in unsafe state after max claim update!")
                save_state()
                return True, f"Maximum claim updated, but system is now in an unsafe state! Consider releasing some resources."
        except ValueError:
            return False, "Invalid value. Please enter a number."

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

# -------------------- FLASK/DASH WEB APPLICATION --------------------

# Initialize Flask server and Dash app
server = Flask(__name__)
server.secret_key = 'resource_manager_secret_key'  # For session management
app = dash.Dash(__name__, server=server, url_base_pathname='/dashboard/')

# Define the layout for the Dash app
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    html.Div(id='page-content')
])

# Define the dashboard layout
dashboard_layout = html.Div([
    html.Div([
        html.H1("ResGuard Resource Management System", style={'textAlign': 'center'}),
        html.Div([
            html.Span("Logged in as: ", style={'fontWeight': 'bold'}),
            html.Span(id='user-display'),
            html.Button('Logout', id='logout-button', style={'marginLeft': '20px'})
        ], style={'textAlign': 'right', 'padding': '10px'}),
        
        dcc.Tabs([
            dcc.Tab(label='Resource Management', children=[
                html.Div([
                    html.Div([
                        html.H3("Request/Release Resources"),
                        html.Div([
                            html.Label("Select Resource:"),
                            dcc.Dropdown(
                                id='resource-dropdown',
                                options=[{'label': res, 'value': res} for res in resources.keys()],
                                value='CPU'
                            ),
                            html.Div([
                                html.Button('Request Resource', id='request-button', 
                                           style={'marginRight': '10px', 'backgroundColor': '#4CAF50', 'color': 'white'}),
                                html.Button('Release Resource', id='release-button',
                                           style={'backgroundColor': '#f44336', 'color': 'white'})
                            ], style={'marginTop': '10px', 'marginBottom': '10px'}),
                            html.Div(id='resource-message', style={'marginTop': '10px', 'padding': '10px', 'border': '1px solid #ddd'})
                        ], style={'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'})
                    ], className='six columns'),
                    
                    html.Div([
                        html.H3("Max Claim Management"),
                        html.Div([
                            html.Label("Select Resource:"),
                            dcc.Dropdown(
                                id='max-claim-resource',
                                options=[{'label': res, 'value': res} for res in resources.keys()],
                                value='CPU'
                            ),
                            html.Label("New Max Claim Value:", style={'marginTop': '10px'}),
                            dcc.Input(id='max-claim-value', type='number', min=0, step=0.1),
                            html.Button('Update Max Claim', id='update-max-claim', 
                                       style={'marginTop': '10px', 'backgroundColor': '#2196F3', 'color': 'white'}),
                            html.Div(id='max-claim-message', style={'marginTop': '10px', 'padding': '10px', 'border': '1px solid #ddd'})
                        ], style={'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'})
                    ], className='six columns')
                ], className='row', style={'marginBottom': '20px'}),
                
                html.Div([
                    html.H3("Your Current Allocations"),
                    html.Div(id='user-allocations', style={'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'})
                ], style={'marginBottom': '20px'})
            ]),
            
            dcc.Tab(label='System Monitor', children=[
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
            ]),
            
            dcc.Tab(label='System Logs', children=[
                html.Div([
                    html.H3("System Activity Logs", style={'textAlign': 'center'}),
                    html.Button('Clear Logs', id='clear-logs-button', 
                               style={'marginBottom': '10px', 'backgroundColor': '#f44336', 'color': 'white'}),
                    html.Div(id="logs-container", style={
                        'height': '400px',
                        'overflowY': 'scroll',
                        'border': '1px solid #ddd',
                        'padding': '10px',
                        'marginBottom': '20px',
                        'fontFamily': 'monospace'
                    })
                ])
            ]),
        ]),
        
        dcc.Interval(id="interval-update", interval=2000, n_intervals=0)
    ], style={'padding': '20px', 'maxWidth': '1200px', 'margin': '0 auto'})
])

# Login page layout
login_layout = html.Div([
    html.H1("ResGuard Resource Management System", style={'textAlign': 'center'}),
    html.Div([
        html.H2("Login", style={'textAlign': 'center'}),
        html.Div([
            html.Label("Username:"),
            dcc.Input(id='username-input', type='text', placeholder='Enter username'),
            html.Label("Password:", style={'marginTop': '10px'}),
            dcc.Input(id='password-input', type='password', placeholder='Enter password'),
            html.Button('Login', id='login-button', style={'marginTop': '20px', 'width': '100%'}),
            html.Div(id='login-message', style={'marginTop': '10px', 'color': 'red'})
        ], style={'width': '300px', 'margin': '0 auto', 'padding': '20px', 'border': '1px solid #ddd', 'borderRadius': '5px'})
    ])
])

# Define Flask routes
@server.route('/')
def index():
    if 'user' in session:
        return redirect('/dashboard/')
    return redirect('/login')

@server.route('/login')
def login():
    return redirect('/dashboard/login')

@server.route('/logout')
def logout():
    if 'user' in session:
        log_event(f"User {session['user']} logged out")
    session.pop('user', None)
    return redirect('/login')

# Define Dash callbacks
@app.callback(
    Output('page-content', 'children'),
    [Input('url', 'pathname')]
)
def display_page(pathname):
    if pathname == '/dashboard/login':
        return login_layout
    elif pathname == '/dashboard/' or pathname == '/dashboard':
        if 'user' in session:
            return dashboard_layout
        else:
            return login_layout
    else:
        return login_layout

@app.callback(
    [Output('login-message', 'children'),
     Output('url', 'pathname')],
    [Input('login-button', 'n_clicks')],
    [State('username-input', 'value'),
     State('password-input', 'value')]
)
def process_login(n_clicks, username, password):
    if n_clicks is None or n_clicks == 0:
        return "", dash.no_update
    
    if not username or not password:
        return "Please enter both username and password", dash.no_update
    
    if username in users and users[username] == password:
        session['user'] = username
        log_event(f"User {username} logged in")
        return "", "/dashboard/"
    else:
        return "Invalid username or password", dash.no_update

@app.callback(
    Output('url', 'pathname', allow_duplicate=True),
    [Input('logout-button', 'n_clicks')],
    prevent_initial_call=True
)
def process_logout(n_clicks):
    if n_clicks:
        if 'user' in session:
            log_event(f"User {session['user']} logged out")
        session.pop('user', None)
        return "/dashboard/login"
    return dash.no_update

@app.callback(
    Output('user-display', 'children'),
    [Input('interval-update', 'n_intervals')]
)
def update_user_display(n):
    if 'user' in session:
        return session['user']
    return "Not logged in"

@app.callback(
    [Output('resource-message', 'children'),
     Output('resource-message', 'style')],
    [Input('request-button', 'n_clicks'),
     Input('release-button', 'n_clicks')],
    [State('resource-dropdown', 'value')],
    prevent_initial_call=True
)
def handle_resource_action(request_clicks, release_clicks, resource):
    ctx = dash.callback_context
    if not ctx.triggered:
        return "", {'padding': '10px', 'border': '1px solid #ddd'}
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if 'user' not in session:
        return "Please log in first", {'padding': '10px', 'border': '1px solid #ddd', 'color': 'red'}
    
    user = session['user']
    
    if button_id == 'request-button':
        success, message = request_resource(user, resource)
    elif button_id == 'release-button':
        success, message = release_resource(user, resource)
    else:
        return "", {'padding': '10px', 'border': '1px solid #ddd'}
    
    style = {
        'padding': '10px', 
        'border': '1px solid #ddd', 
        'color': 'green' if success else 'red',
        'backgroundColor': '#f0fff0' if success else '#fff0f0'
    }
    
    return message, style

@app.callback(
    [Output('max-claim-message', 'children'),
     Output('max-claim-message', 'style')],
    [Input('update-max-claim', 'n_clicks')],
    [State('max-claim-resource', 'value'),
     State('max-claim-value', 'value')],
    prevent_initial_call=True
)
def handle_max_claim_update(n_clicks, resource, value):
    if not n_clicks:
        return "", {'padding': '10px', 'border': '1px solid #ddd'}
    
    if 'user' not in session:
        return "Please log in first", {'padding': '10px', 'border': '1px solid #ddd', 'color': 'red'}
    
    if value is None:
        return "Please enter a value", {'padding': '10px', 'border': '1px solid #ddd', 'color': 'red'}
    
    user = session['user']
    success, message = update_max_claim(user, resource, value)
    
    style = {
        'padding': '10px', 
        'border': '1px solid #ddd', 
        'color': 'green' if success else 'red',
        'backgroundColor': '#f0fff0' if success else '#fff0f0'
    }
    
    return message, style

@app.callback(
    Output('user-allocations', 'children'),
    [Input('interval-update', 'n_intervals')]
)
def update_user_allocations(n):
    if 'user' not in session:
        return "Please log in to view your allocations"
    
    user = session['user']
    allocation_rows = []
    
    for res in resources:
        allocation_rows.append(html.Tr([
            html.Td(res),
            html.Td(f"{allocation[user][res]:.2f}"),
            html.Td(f"{max_claim[user][res]:.2f}"),
            html.Td(f"{(allocation[user][res] / max_claim[user][res] * 100):.1f}%" if max_claim[user][res] > 0 else "0%")
        ]))
    
    table = html.Table([
        html.Thead(
            html.Tr([
                html.Th("Resource"),
                html.Th("Current Allocation"),
                html.Th("Maximum Claim"),
                html.Th("Usage Percentage")
            ])
        ),
        html.Tbody(allocation_rows)
    ], style={'width': '100%', 'borderCollapse': 'collapse'})
    
    return table

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
    
    # System Usage Graph
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

# -------------------- WEB-BASED GUI --------------------

def start_web_app():
    global dashboard_server_running
    if not dashboard_server_running:
        dashboard_server_running = True
        # Open browser after a short delay to ensure the server is running
        threading.Timer(1.5, lambda: webbrowser.open_new("http://127.0.0.1:8050")).start()
        
        # Create the Dash app
        app.layout = html.Div([
            html.H1("Cloud Resource Manager with Real-time Monitoring", className="app-header"),
            
            # Login Section
            html.Div([
                html.H2("Login"),
                html.Div([
                    html.Label("Username:"),
                    dcc.Input(id="username-input", type="text", placeholder="Enter username"),
                    html.Br(),
                    html.Label("Password:"),
                    dcc.Input(id="password-input", type="password", placeholder="Enter password"),
                    html.Br(),
                    html.Button("Login", id="login-button", n_clicks=0),
                    html.Div(id="login-status")
                ], id="login-container")
            ], id="login-section"),
            
            # Resource Management Section
            html.Div([
                html.H2("Resource Management"),
                html.Div([
                    html.Label("Current User:"),
                    html.Div(id="current-user", style={"fontWeight": "bold"}),
                    html.Br(),
                    html.Label("Select Resource:"),
                    dcc.Dropdown(
                        id="resource-dropdown",
                        options=[{"label": res, "value": res} for res in resources.keys()],
                        value="CPU"
                    ),
                    html.Br(),
                    html.Button("Request Resource", id="request-button", n_clicks=0),
                    html.Button("Release Resource", id="release-button", n_clicks=0, style={"marginLeft": "10px"}),
                    html.Div(id="resource-status", style={"marginTop": "10px"})
                ]),
                
                # System Monitor
                html.Div([
                    html.H3("System Monitor"),
                    dcc.Graph(id="system-usage-graph"),
                    html.Div(id="system-info")
                ]),
                
                # Banker's Algorithm Status
                html.Div([
                    html.H3("Banker's Algorithm Status"),
                    html.Div(id="safety-status"),
                    html.Div(id="safe-sequence")
                ]),
                
                # User Allocations
                html.Div([
                    html.H3("Your Allocations"),
                    html.Div(id="user-allocations")
                ]),
                
                # Max Claim Management
                html.Div([
                    html.H3("Max Claim Management"),
                    html.Label("Resource:"),
                    dcc.Dropdown(
                        id="max-claim-resource",
                        options=[{"label": res, "value": res} for res in resources.keys()],
                        value="CPU"
                    ),
                    html.Label("New Max:"),
                    dcc.Input(id="max-claim-value", type="number", min=0, step=0.1),
                    html.Button("Update Max Claim", id="update-max-claim-button", n_clicks=0),
                    html.Div(id="max-claim-status")
                ]),
                
                # Refresh Button
                html.Button("Refresh Data", id="refresh-button", n_clicks=0, style={"marginTop": "20px"})
            ], id="resource-section", style={"display": "none"}),
            
            # System Logs Section
            html.Div([
                html.H2("System Logs"),
                html.Button("Clear Logs", id="clear-logs-button", n_clicks=0),
                html.Div(id="logs-container", style={"maxHeight": "400px", "overflowY": "auto", "border": "1px solid #ddd", "padding": "10px", "marginTop": "10px"})
            ]),
            
            # Store the current user
            dcc.Store(id="user-store"),
            
            # Interval for periodic updates
            dcc.Interval(id="interval-component", interval=2000, n_intervals=0)  # Update every 2 seconds
        ])
        
        # Callbacks
        
        # Login callback
        @app.callback(
            [Output("login-status", "children"),
             Output("user-store", "data"),
             Output("login-section", "style"),
             Output("resource-section", "style")],
            [Input("login-button", "n_clicks")],
            [State("username-input", "value"),
             State("password-input", "value")]
        )
        def authenticate(n_clicks, username, password):
            if n_clicks == 0:
                return "", None, {"display": "block"}, {"display": "none"}
            
            if username in users and users[username] == password:
                log_event(f"User {username} logged in")
                return html.Div(f"Logged in as {username}", style={"color": "green"}), username, {"display": "none"}, {"display": "block"}
            else:
                return html.Div("Invalid username or password!", style={"color": "red"}), None, {"display": "block"}, {"display": "none"}
        
        # Display current user
        @app.callback(
            Output("current-user", "children"),
            [Input("user-store", "data")]
        )
        def update_current_user(user):
            return user if user else "Not logged in"
        
        # Request resource callback
        @app.callback(
            Output("resource-status", "children"),
            [Input("request-button", "n_clicks"),
             Input("release-button", "n_clicks")],
            [State("user-store", "data"),
             State("resource-dropdown", "value")]
        )
        def handle_resource_actions(request_clicks, release_clicks, user, resource):
            ctx = dash.callback_context
            if not ctx.triggered:
                return ""
            
            button_id = ctx.triggered[0]["prop_id"].split(".")[0]
            
            if not user:
                return html.Div("Please login first!", style={"color": "red"})
            
            if button_id == "request-button" and request_clicks > 0:
                if request_resource(user, resource):
                    return html.Div(f"Successfully allocated {resource} to {user}", style={"color": "green"})
                else:
                    return html.Div(f"Failed to allocate {resource} to {user}", style={"color": "red"})
            
            elif button_id == "release-button" and release_clicks > 0:
                if release_resource(user, resource):
                    return html.Div(f"Successfully released {resource} from {user}", style={"color": "green"})
                else:
                    return html.Div(f"Failed to release {resource} from {user}", style={"color": "red"})
            
            return ""
        
        # Update system info
        @app.callback(
            [Output("system-info", "children"),
             Output("system-usage-graph", "figure"),
             Output("safety-status", "children"),
             Output("safe-sequence", "children")],
            [Input("interval-component", "n_intervals"),
             Input("refresh-button", "n_clicks")]
        )
        def update_system_info(n_intervals, n_clicks):
            # Update system resources
            update_system_resources()
            
            # System info
            system_info = [
                html.Div(f"CPU: {resources['CPU']:.2f} units available"),
                html.Div(f"Memory: {resources['Memory']:.2f} GB available"),
                html.Div(f"Disk: {resources['Disk']:.2f} GB available"),
                html.Div(f"Network: {resources['Network']:.2f} units available")
            ]
            
            # System usage graph
            system_data = []
            for res, value in resources.items():
                # Convert available resources to usage percentage
                if res == "CPU":
                    cpu_count = psutil.cpu_count(logical=True)
                    usage_percent = 100 - (resources[res] / cpu_count * 100)
                    system_data.append({"Resource": res, "Usage": min(100, max(0, usage_percent))})
                elif res == "Memory":
                    total_memory = psutil.virtual_memory().total / (1024 ** 3)
                    usage_percent = 100 - (resources[res] / total_memory * 100)
                    system_data.append({"Resource": res, "Usage": min(100, max(0, usage_percent))})
                elif res == "Disk":
                    total_disk = psutil.disk_usage('/').total / (1024 ** 3)
                    usage_percent = 100 - (resources[res] / total_disk * 100)
                    system_data.append({"Resource": res, "Usage": min(100, max(0, usage_percent))})
                else:
                    # For Network, just use a placeholder
                    system_data.append({"Resource": res, "Usage": 100 - resources[res]})
            
            df_system = pd.DataFrame(system_data)
            
            fig_system = go.Figure()
            fig_system.add_trace(go.Bar(
                x=df_system["Resource"],
                y=df_system["Usage"],
                marker_color=['blue' if x < 70 else 'orange' if x < 90 else 'red' for x in df_system["Usage"]],
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
                    html.Div("✅ System is in a SAFE state", style={'color': 'green', 'fontWeight': 'bold'})
                ]
                safe_sequence_text = html.Div(f"Safe Sequence: {' → '.join(safe_sequence)}")
            else:
                safety_status = [
                    html.Div("⚠️ System is in an UNSAFE state", style={'color': 'red', 'fontWeight': 'bold'})
                ]
                safe_sequence_text = html.Div("Warning: Potential deadlock condition! Consider releasing resources.")
            
            return system_info, fig_system, safety_status, safe_sequence_text
        
        # Update user allocations
        @app.callback(
            Output("user-allocations", "children"),
            [Input("interval-component", "n_intervals"),
             Input("refresh-button", "n_clicks"),
             Input("resource-status", "children")],
            [State("user-store", "data")]
        )
        def update_user_allocations(n_intervals, n_clicks, resource_status, user):
            if not user:
                return "Please login to view your allocations"
            
            alloc_items = []
            for res in resources:
                alloc_items.append(
                    html.Div(f"{res}: {allocation[user][res]:.2f} / {max_claim[user][res]:.2f} (used/max)")
                )
            
            return alloc_items
        
        # Update max claim
        @app.callback(
            Output("max-claim-status", "children"),
            [Input("update-max-claim-button", "n_clicks")],
            [State("user-store", "data"),
             State("max-claim-resource", "value"),
             State("max-claim-value", "value")]
        )
        def update_max_claim_callback(n_clicks, user, resource, new_max):
            if n_clicks == 0:
                return ""
            
            if not user:
                return html.Div("Please login first!", style={"color": "red"})
            
            if not resource:
                return html.Div("Please select a resource!", style={"color": "red"})
            
            if new_max is None:
                return html.Div("Please enter a valid value!", style={"color": "red"})
            
            try:
                if new_max < 0:
                    return html.Div("Max claim must be a positive number", style={"color": "red"})
                
                if update_max_claim(user, resource, new_max):
                    return html.Div(f"Maximum claim for {resource} updated to {new_max}", style={"color": "green"})
                else:
                    return html.Div("Failed to update max claim", style={"color": "red"})
            except Exception as e:
                return html.Div(f"Error: {str(e)}", style={"color": "red"})
        
        # Update logs
        @app.callback(
            Output("logs-container", "children"),
            [Input("interval-component", "n_intervals"),
             Input("clear-logs-button", "n_clicks")]
        )
        def update_logs(n_intervals, n_clicks):
            # Clear logs if button was clicked
            if dash.callback_context.triggered_id == "clear-logs-button" and n_clicks > 0:
                global logs
                logs = []
                save_state()
                return [html.P("Logs cleared")]
            
            # Display logs
            return [html.P(log) for log in logs[-50:]]  # Show last 50 logs
        
        # Start the system monitor in a separate thread
        monitor_thread = threading.Thread(target=system_monitor, daemon=True)
        monitor_thread.start()
        
        # Run the app
        app.run_server(debug=False, use_reloader=False, port=8050)

def main():
    # Load previous state
    load_state()
    
    # Start the web app
    start_web_app()

if __name__ == "__main__":
    main()