# ===== scripts/setup.py =====
#!/usr/bin/env python3
"""Setup script for the arbitrage bot"""

from setuptools import setup, find_packages

setup(
    name="solana-arbitrage-bot",
    version="2.0.0",
    author="Your Name",
    description="Production-ready Solana arbitrage bot",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        line.strip()
        for line in open("requirements.txt")
        if line.strip() and not line.startswith("#")
    ],
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "arbitrage-bot=arbitrage_bot:main",
            "arbitrage-monitor=simple_monitor:main",
        ],
    },
)