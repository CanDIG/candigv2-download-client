#!/usr/bin/env python3
import csv
import os
import httpx
import getpass
from httpx import HTTPError
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.box import DOUBLE
from rich.align import Align
import jwt
from datetime import datetime

# Initialize Rich console
console = Console()

def flatten_json(nested_json, prefix=""):
    """
    Flatten a nested JSON structure into a flat dictionary
    """
    out = {}
    for key, value in nested_json.items():
        if isinstance(value, dict):
            # If the value is a dict, recursively flatten it
            flattened = flatten_json(value, prefix + key + "_")
            out.update(flattened)
        else:
            # Otherwise just add the key-value pair
            out[prefix + key] = value
    return out

def get_api_data(katsu_path, auth_token, federation_url):
    """
    Fetches data from the API using the provided authentication token using httpx.
    Can make direct GET requests or POST to federation endpoint based on parameters.
    """
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
  
    try:
        with httpx.Client(timeout=30.0) as client:
            # If katsu_path is provided, use federation endpoint
            if katsu_path:
                # Prepare federation payload
                payload = {
                    "path": katsu_path,
                    "payload": {},
                    "method": "GET",
                    "service": "katsu"
                }
                
                # Make POST request to federation endpoint
                response = client.post(federation_url, headers=headers, json=payload)

            response.raise_for_status()
            return response.json()
    except HTTPError as e:
        console.print(f"[bold red]Error fetching data from API:[/] {e}")
        return None
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/] {e}")
        return None

def get_available_programs(auth_token, federation_url):
    """
    Fetch available programs from the API
    """
    katsu_path = "v3/authorized/programs/"
    
    with console.status("[bold green]Fetching programs...[/]", spinner="dots"):
        data = get_api_data(katsu_path, auth_token, federation_url)

    if not data:
        console.print("[bold red]Failed to retrieve program list[/]")
        return []

    programs = []
    # The response is now a list with federation results
    for location_data in data:
        # Extract the results section that contains the items
        results = location_data.get("results", {})
        
        # Get the items from the results
        items = results.get("items", [])
        
        # Process each program item
        for item in items:
            program_id = item.get("program_id")
            if program_id:
                programs.append(program_id)

    return programs


def get_program_selection(programs):
    """
    Provides a user-friendly interface for selecting multiple programs
    """
    if not programs:
        return []

    # Create a table for displaying the programs
    table = Table()
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Program", style="green")
    
    for i, program_id in enumerate(programs, 1):
        table.add_row(f"{i}", program_id)
    
    console.print(table)

    # Display selection instructions
    console.print(Panel(
        "[bold yellow]You can select multiple programs using the following formats:[/]\n"
        "  • Single program: [cyan]1[/]\n"
        "  • Multiple programs: [cyan]1,3,5[/]\n"
        "  • Range of programs: [cyan]1-4[/]\n"
        "  • Combination: [cyan]1,3-5,7[/]\n"
        "  • All programs: [cyan]all[/] or [cyan]*[/]",
        title="Selection Options",
        border_style="blue"
    ))

    while True:
        selection = Prompt.ask("\nSelect program(s)", default="all")
        selection = selection.strip().lower()

        # Handle "all" or "*" selection
        if selection == "all" or selection == "*":
            return programs

        # Parse the selection string
        try:
            selected_indices = set()

            # Split by comma
            for part in selection.split(","):
                part = part.strip()

                # Handle ranges (e.g., "1-4")
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > len(programs) or start > end:
                        console.print(f"[bold red]Invalid range:[/] {part}. Valid range is 1-{len(programs)}.")
                        break
                    selected_indices.update(range(start, end + 1))
                # Handle single numbers
                else:
                    try:
                        idx = int(part)
                        if idx < 1 or idx > len(programs):
                            console.print(f"[bold red]Invalid selection:[/] {idx}. Valid range is 1-{len(programs)}.")
                            break
                        selected_indices.add(idx)
                    except ValueError:
                        console.print(f"[bold red]Invalid input:[/] {part}. Please enter numbers only.")
                        break
            else:
                # This executes if the loop completed without a break
                if selected_indices:
                    # Convert 1-based indices to 0-based and get the corresponding programs
                    selected_programs = [programs[i - 1] for i in sorted(selected_indices)]

                    # Display selected programs for confirmation
                    selection_table = Table()
                    selection_table.add_column("Program", style="green")
                    
                    for prog in selected_programs:
                        selection_table.add_row(prog)
                    
                    console.print(selection_table)

                    if Confirm.ask("Proceed with these selections?"):
                        return selected_programs
                else:
                    console.print("[bold red]No valid selections made.[/]")

        except Exception as e:
            console.print(f"[bold red]Error processing selection:[/] {e}")

        console.print("[yellow]Please try again.[/]")


def process_program_data(auth_token, selected_programs, federation_url):
    """
    Process and export data for selected programs to a single CSV file
    """
    if not selected_programs:
        console.print("[bold red]No programs selected. Exiting.[/]")
        return

    # Define a single output file for all programs
    output_file = "output.csv"

    # List to store all flattened items
    all_flattened_items = []
    # Set to track all fields across all programs
    all_keys = set()

    # Process each selected program
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[bold green]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[yellow]Processing programs...", total=len(selected_programs))
        
        for selected_program in selected_programs:
            # Update task description
            progress.update(task, description=f"Processing program {selected_program}")
            
            # Use query parameter to filter by program_id
            katsu_path = f"v3/authorized/donors/?program_id={selected_program}&page_size=100000"

            # Fetch data
            data = get_api_data(katsu_path, auth_token, federation_url)

            if not data:
                progress.console.print(f"[bold red]Failed to retrieve data for program {selected_program}[/]")
                progress.advance(task)
                continue

            # Process each location in the federated response
            program_items = []
            for location_data in data:
                # Extract the results section that contains the items
                results = location_data.get("results", {})
                
                # Get the items from the results and add to our collection
                items = results.get("items", [])
                program_items.extend(items)
                
            if not program_items:
                progress.console.print(f"[bold yellow]No donor records found for program {selected_program}[/]")
                progress.advance(task)
                continue

            # Flatten each item
            flattened_items = [flatten_json(item) for item in program_items]
            all_flattened_items.extend(flattened_items)

            # Update our set of all keys
            for item in flattened_items:
                all_keys.update(item.keys())
                
            progress.console.print(f"[green]✓[/] Processed {len(program_items)} records for program {selected_program}")
            progress.advance(task)

    if not all_flattened_items:
        console.print("[bold red]No donor records found for any of the selected programs.[/]")
        return

    console.print(f"\n[bold blue]Combining data from all programs ([green]{len(all_flattened_items)}[/] total records)...[/]")

    # Define ordered fields - start with required order
    ordered_fields = [
        "program_id",
        "submitter_donor_id",
        "gender",
        "sex_at_birth",
        "is_deceased",
        "lost_to_followup_after_clinical_event_identifier",
        "lost_to_followup_reason",
        "date_alive_after_lost_to_followup_day_interval",
        "date_alive_after_lost_to_followup_month_interval",
        "cause_of_death",
        "date_of_birth_day_interval",
        "date_of_birth_month_interval",
        "date_of_death_day_interval",
        "date_of_death_month_interval",
        "date_resolution",
    ]

    # Add remaining fields in alphabetical order
    remaining_fields = sorted([key for key in all_keys if key not in ordered_fields])
    header = ordered_fields + remaining_fields

    # Write all data to a single CSV file
    try:
        with console.status("[bold green]Writing data to CSV...[/]", spinner="dots"):
            with open(output_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                writer.writerows(all_flattened_items)

        file_size = os.path.getsize(output_file)
        size_display = f"{file_size / 1024:.1f} KB" if file_size > 1024 else f"{file_size} bytes"
        console.print(f"[bold green]✓ Successfully exported to [cyan]{output_file}[/] ([yellow]{size_display}[/])[/]")
    except Exception as e:
        console.print(f"[bold red]⨯ Error writing CSV file:[/] {e}")

def check_token_expiration(auth_token):
    """
    Decodes the JWT token and checks its expiration time.
    Returns True if token is valid, False if expired or invalid.
    """
    try:
        with console.status("[bold blue]Checking token validity...[/]", spinner="bouncingBar"):
            # JWT tokens are typically in three parts separated by dots
            token_parts = auth_token.split('.')
            if len(token_parts) != 3:
                console.print("[yellow]Provided token doesn't appear to be a valid JWT token.[/]")
                return False
                
            # Decode without verification - we're just reading the payload
            payload = jwt.decode(auth_token, options={"verify_signature": False})
        
        # Check for expiration claim
        if 'exp' in payload:
            exp_timestamp = payload['exp']
            exp_datetime = datetime.fromtimestamp(exp_timestamp)
            current_time = datetime.now()
            
            # Calculate time remaining
            time_remaining = exp_datetime - current_time
            minutes_remaining = (time_remaining.seconds % 3600) // 60
            
            token_table = Table()
            token_table.add_column("Token expires at", style="cyan")
            token_table.add_column("Time remaining", style="green")
            
            if time_remaining.total_seconds() > 0:
                token_table.add_row(exp_datetime.strftime('%Y-%m-%d %H:%M:%S'), f"{minutes_remaining} minutes")
                console.print(token_table)
                return True
            else:
                token_table.add_row(exp_datetime.strftime('%Y-%m-%d %H:%M:%S'), "[bold red]EXPIRED![/]")
                console.print(token_table)
                return False
        else:
            console.print("[yellow]Token doesn't contain expiration information.[/]")
            return True
    except Exception as e:
        console.print(f"[bold yellow]Couldn't decode token:[/] {e}")
        return False
    
def main():
    title = "[bold white]  WELCOME  "
    panel_content = Align.center(
        "\n\n[bold cyan]CANDIG Data Export Tool[/]\n\n"
    )
    
    console.print(Panel(
        panel_content,
        box=DOUBLE,
        border_style="blue",
        padding=(0, 2),
        title=title,
        subtitle="[dim] v0.1 [/]",
        title_align="center",
        subtitle_align="right",
        width=80
    ))

    # Define base API endpoint
    default_url = "http://candig.docker.internal:5080"
    base_url = Prompt.ask(
        "\nEnter the base URL of the CANDIG server", 
        default=default_url
    )
    federation_url = f"{base_url}/federation/v1/fanout"

    # Prompt user for authentication token on a new line
    auth_token = getpass.getpass("\nEnter your token: ")

    if not auth_token:
        console.print("[bold red]No authentication token provided. Exiting.[/]")
        return
    
    # Check token expiration
    if not check_token_expiration(auth_token):
        console.print("[bold red]Exiting due to invalid token.[/]")
        return
    
    try:
        # Get available programs
        console.print("[bold]Fetching available programs...[/]")
        programs = get_available_programs(auth_token, federation_url)

        if not programs:
            console.print("[bold red]No programs available. Exiting.[/]")
            return

        # Get user selection
        selected_programs = get_program_selection(programs)

        # Process and export data for selected programs
        process_program_data(auth_token, selected_programs, federation_url)

        console.print(Panel("[bold green]Export successfully completed![/]", border_style="green"))

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Operation cancelled by user.[/]")
    except Exception as e:
        console.print(f"[bold red]An error occurred:[/] {e}")


if __name__ == "__main__":
    main()