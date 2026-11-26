"""Gunicorn WSGI entry point; configuration is read from a root-managed file."""
import os
from pathlib import Path
from .config import load
from .web import Application

config=load(Path(os.environ.get('SUNHILL_CONFIG','/opt/sunhill-nvr/config/config.json')))
application=Application(config.database,config.archive,config.exports,allowed_origin=config.origin,archive_lock=config.archive_lock,timezone=config.timezone,control_state=config.control_state,trust_client_header=True,minimum_free=config.minimum_free,free_buffer=config.free_buffer,staging_root=config.staging)
