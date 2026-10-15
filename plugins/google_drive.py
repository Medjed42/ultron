"""Ultron integration plugin; no network activity during import."""
from core.integrations import drive as service
from core.integrations.common import dispatch, validate

PLUGIN = {'name': 'google_drive',
 'version': '2.0.0',
 'description': 'Analyze Google Drive storage, largest files, checksum-based duplicate candidates and '
                'shared-account quota. Opens Drive dashboard. Selected-file trash always requires a desktop '
                'review; never permanently deletes.',
 'behavior': 'NON_BLOCKING',
 'capabilities': ['open_panel', 'analyze', 'trash'],
 'authentication': 'Google OAuth (read metadata; separate write grant for cleanup)',
 'permissions': ['drive'],
 'panel': 'drive',
 'strict_inputs': True,
 'parameters': {'type': 'OBJECT',
                'properties': {'action': {'type': 'STRING', 'enum': ['open_panel', 'analyze', 'trash']},
                               'file_ids': {'type': 'ARRAY', 'items': {'type': 'STRING'}}},
                'required': ['action']}}

def initialize():
    """Passive lifecycle: authorization is always human initiated."""
    return None

def shutdown():
    return None

def run(parameters, player=None, session_memory=None):
    validate(parameters, PLUGIN["parameters"])
    return dispatch("drive", parameters, service.handle)
