"""Local operator configuration, shared with Pi; never loaded from an Issue."""
import json
from pathlib import Path

CEILINGS = {
    'workspace_max_entries': 250000, 'workspace_max_bytes': 8 * 1024**3,
    'catalog_max_files': 250000, 'catalog_max_path_bytes': 32 * 1024**2,
    'source_context_max_files': 128, 'source_file_max_bytes': 2 * 1024**2,
    'planning_packet_max_bytes': 256 * 1024**2, 'discovery_max_files': 2000,
    'discovery_max_bytes': 128 * 1024**2, 'discovery_catalog_max_entries': 250000,
    'discovery_seconds': 600, 'candidate_max_rounds': 30,
    'test_timeout_max_seconds': 7200, 'native_session_max_bytes': 1024**3,
}

def validate_limits(values):
    if (not isinstance(values, dict) or set(values) != set(CEILINGS)
            or any(type(v) is not int or not 1 <= v <= CEILINGS[k] for k, v in values.items())
            or values['source_file_max_bytes'] > values['discovery_max_bytes']
            or values['source_file_max_bytes'] * values['source_context_max_files'] > values['planning_packet_max_bytes']
            or values['catalog_max_files'] > values['discovery_catalog_max_entries']):
        raise ValueError('Invalid maintenance resource limits configuration')
    return dict(values)

LIMITS = validate_limits(json.loads(Path(__file__).with_name('maintenance_limits.json').read_text(encoding='utf-8')))

def capacity_view():
    return dict(LIMITS)
