"""Explicit demo switch; folder allowlists remain enforced."""
import os

def demo_mode():return os.environ.get('DOCAGENT_DEMO_MODE','0')=='1'
