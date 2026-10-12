"""Classic metadata also supports Ubuntu 22.04's distribution setuptools."""
from setuptools import find_packages, setup

setup(
    name='holdnote',version='0.1.0',
    description='Hold Super, select text, release to save privately to Memos.',
    python_requires='>=3.10',license='MIT',
    packages=find_packages(include=['holdnote','holdnote.*']),
    install_requires=['requests>=2.25,<3','python-xlib>=0.29,<1','keyring>=23,<26','cryptography>=3.4'],
    entry_points={'console_scripts':['holdnote=holdnote.cli:main']},
)
