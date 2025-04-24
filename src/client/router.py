# api_strategies.py
"""
Defines strategies for interacting with APIs: one for live calls, one for TEST RUNs.
"""

import httpx
import json
import os
import sys
from typing import Dict, Any, Optional, List

# Import config for paths used in live strategy
import config


class CandigRouter:
    """Handles actual live API interactions."""

    def execute_federation_call(
        self,
        federation_url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout: float
    ) -> Optional[List[Dict[str, Any]]]:
        """Makes a real POST request to the federation endpoint."""
        service = payload.get("service", "unknown")
        path = payload.get("path", "N/A")
        print(f"Sending request to federation for {service} service ({path})...")
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(federation_url, headers=headers, json=payload)
                response.raise_for_status()
                print(f"Request for {service} successful (Status: {response.status_code}).")
                return response.json()
        except httpx.HTTPStatusError as e:
            print(f"HTTP error during {service} request: {e.response.status_code}", file=sys.stderr)
            try:
                details = e.response.json()
                print(f"Error details: {json.dumps(details, indent=2)}", file=sys.stderr)
            except json.JSONDecodeError:
                print(f"Response body: {e.response.text[:500]}...", file=sys.stderr)
            return None
        except httpx.RequestError as e:
            print(f"Network/connection error during {service} request: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Unexpected error during {service} request: {e}", file=sys.stderr)
            return None

class TestRunRouter:
    """Simulates API interactions using mock data."""

    def __init__(self, mock_dir: str):
        self.mock_dir = mock_dir
        print("\n******************************************************")
        print("*** TEST RUN MODE ACTIVATED (No token provided) ***")
        print(f"*** API calls simulated using data from: {self.mock_dir} ***")
        print("******************************************************")
        if not os.path.isdir(self.mock_dir):
            print(f"\nError: Mock data directory not found: {self.mock_dir}", file=sys.stderr)
            print("Please create the 'mock' directory and add sample JSON response files.", file=sys.stderr)
            sys.exit(1)

    def execute_federation_call(
        self,
        federation_url: str, 
        headers: Dict[str, str], 
        payload: Dict[str, Any],
        timeout: float 
    ) -> Optional[List[Dict[str, Any]]]:
        """Loads and filters mock data from a file based on the service and payload."""
        service = payload.get("service", "unknown")
        if service == "katsu":
            mock_filename = "clinical.json"
        elif service == "beacon":
             mock_filename = "beacon.json" 
             print("[TEST RUN] Warning: Beacon filtering simulation might be basic.")
        else:
            mock_filename = f"{service}.json"

        mock_filepath = os.path.join(self.mock_dir, mock_filename)

        print(f"[TEST RUN] Simulating federation call for {service} service.")
        print(f"[TEST RUN] Attempting to load mock data from: {mock_filepath}")

        try:
            with open(mock_filepath, 'r', encoding='utf-8') as f:
                mock_data = json.load(f)
            print(f"[TEST RUN] Successfully loaded mock data for {service}.")

            if not isinstance(mock_data, list) or not mock_data:
                 print(f"[TEST RUN] Warning: Mock data in {mock_filepath} is not a list or is empty.", file=sys.stderr)
                 return mock_data 

            # --- Apply Filtering ---
            if service == "katsu":
                print(f"[TEST RUN] Applying filters based on payload: {payload.get('payload', {})}")
                filtered_results = self._filter_mock_data(mock_data[0].get('results', {}), payload.get('payload', {}))
                # Return the filtered data in the original list structure
                return [{**mock_data[0], "results": filtered_results}]
            else:
                 print("[TEST RUN] Skipping detailed filtering for non-Katsu service.")
                 return mock_data


        except FileNotFoundError:
            print(f"[TEST RUN] Error: Mock file not found: {mock_filepath}", file=sys.stderr)
            return None
        except json.JSONDecodeError as e:
            print(f"[TEST RUN] Error: Could not decode JSON from mock file {mock_filepath}: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[TEST RUN] Error loading/filtering mock file {mock_filepath}: {e}", file=sys.stderr)
            return None

    def _filter_mock_data(self, results_data: Dict[str, List[Dict]], filters: Dict[str, Any]) -> Dict[str, List[Dict]]:
        """Applies filtering logic to the loaded mock data based on payload filters."""
        if not filters or not results_data:
            print("[TEST RUN] No filters provided or no results data found. Returning all mock data.")
            return results_data 

        # Extract filters from payload
        program_ids = set(filters.get("program_id") or [])
        # biosample_ids = set(filters.get("biosample_id") or [])
        primary_sites = set(filters.get("primary_site") or [])
        drug_names = set(filters.get("drug_name") or [])
        treatment_types = set(filters.get("treatment_type") or []) 

        # --- Filter by Program ID ---
        filtered_donors = results_data.get('donors', [])
        if program_ids:
            filtered_donors = [d for d in filtered_donors if d.get('program_id') in program_ids]
        filtered_donor_ids = {d.get('submitter_donor_id') for d in filtered_donors}

        # --- Filter Primary Diagnoses ---
        filtered_diagnoses = results_data.get('primary_diagnoses', [])
        # Filter by donors first
        filtered_diagnoses = [diag for diag in filtered_diagnoses if diag.get('submitter_donor_id') in filtered_donor_ids]
        if primary_sites:
            filtered_diagnoses = [diag for diag in filtered_diagnoses if diag.get('primary_site') in primary_sites]
            print(f"[TEST RUN] Filtered diagnoses by primary_site: {len(filtered_diagnoses)} remaining.")
        # filtered_diagnosis_ids = {diag.get('submitter_primary_diagnosis_id') for diag in filtered_diagnoses}
        filtered_donor_ids = {d.get('submitter_donor_id') for d in filtered_diagnoses}

        # --- Filter Treatments ---
        filtered_treatments = results_data.get('treatments', [])
        filtered_treatments = [t for t in filtered_treatments if t.get('submitter_donor_id') in filtered_donor_ids]
        # print(f"[TEST RUN] Filtered treatments by diagnosis: {len(filtered_treatments)} remaining.")
        if treatment_types:
  
            filtered_treatments = [t for t in filtered_treatments if t.get('treatment_type') in treatment_types]
            print(f"[TEST RUN] Filtered treatments by treatment_type: {len(filtered_treatments)} remaining.")
        # filtered_treatment_ids = {t.get('submitter_treatment_id') for t in filtered_treatments} # Assuming treatments have this ID
        filtered_donor_ids = {d.get('submitter_donor_id') for d in filtered_treatments}

        # --- Filter Systemic Therapies (Example - requires 'drug_name' field in mock data) ---
        filtered_systemic_therapies = results_data.get('systemic_therapies', [])
        # Filter by treatments first (assuming systemic_therapies link to treatments)
        filtered_systemic_therapies = [st for st in filtered_systemic_therapies if st.get('submitter_donor_id') in filtered_donor_ids]
        # print(f"[TEST RUN] Filtered systemic_therapies by treatment: {len(filtered_systemic_therapies)} remaining.")
        if drug_names:
          
            filtered_systemic_therapies = [st for st in filtered_systemic_therapies if st.get('drug_name') in drug_names]
            # print(f"[TEST RUN] Filtered systemic_therapies by drug_name: {len(filtered_systemic_therapies)} remaining.")
        filtered_donor_ids = {d.get('submitter_donor_id') for d in filtered_systemic_therapies}

        filtered_donors = [d for d in filtered_donors if d.get('submitter_donor_id') in filtered_donor_ids]
        final_filtered_results = {
            'donors': filtered_donors,
            **{k: v for k, v in results_data.items() if k not in [
                'donors',
            ]}
        }

        return final_filtered_results