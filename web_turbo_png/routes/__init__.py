"""
Turbo PNG Web Application Routes Module
"""
from .main_routes import main_bp
from .upload_routes import upload_bp
from .api_routes import api_bp

__all__ = ['main_bp', 'upload_bp', 'api_bp']
