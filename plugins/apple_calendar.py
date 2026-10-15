"""Ultron integration plugin; no network activity during import."""
from core.integrations import calendar as service
from core.integrations.common import dispatch, validate

PLUGIN = {'name': 'apple_calendar',
 'version': '2.0.0',
 'description': 'Read iCloud Calendar via authorized CalDAV on Windows or a read-only subscription. Opens '
                'calendar panel, expands recurring events and handles timezones. Changes require desktop review. '
                'Use observed calendar IDs; recurrence changes must explicitly target the entire series.',
 'behavior': 'NON_BLOCKING',
 'capabilities': ['open_panel', 'read', 'create', 'update', 'delete'],
 'authentication': 'iCloud app-specific password or read-only ICS',
 'permissions': ['calendar'],
 'panel': 'calendar',
 'strict_inputs': True,
 'parameters': {'type': 'OBJECT',
                'properties': {'action': {'type': 'STRING',
                                          'enum': ['open_panel', 'read', 'create', 'update', 'delete']},
                               'start': {'type': 'STRING'},
                               'end': {'type': 'STRING'},
                               'timezone': {'type': 'STRING'},
                               'calendar_id': {'type': 'STRING'},
                               'uid': {'type': 'STRING'},
                               'title': {'type': 'STRING'},
                               'location': {'type': 'STRING'},
                               'scope': {'type': 'STRING', 'enum': ['single', 'series']},
                               'recurrence': {'type': 'STRING', 'enum': ['DAILY', 'WEEKLY', 'MONTHLY']},
                               'count': {'type': 'INTEGER'},
                               'reminder_minutes': {'type': 'INTEGER'}},
                'required': ['action']}}

def initialize():
    """Passive lifecycle: authorization is always human initiated."""
    return None

def shutdown():
    return None

def run(parameters, player=None, session_memory=None):
    validate(parameters, PLUGIN["parameters"])
    return dispatch("calendar", parameters, service.handle)
