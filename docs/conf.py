#!/usr/bin/env python3

# Configuration file for the Sphinx documentation builder.

# -- Path setup --------------------------------------------------------------

import datetime
import importlib.metadata
import os
import sys

from sphinx.ext.intersphinx import missing_reference

sys.path.insert(0, os.path.abspath('../src'))

# Verify the source path exists
if not os.path.exists(os.path.abspath('../src')):
    import warnings
    warnings.warn("Source directory '../src' not found. API documentation may be "
                  'incomplete.', stacklevel=1)

# -- Project information -----------------------------------------------------

project = 'rms-host-voyager'
copyright = f'{datetime.date.today().year}, SETI Institute'
author = 'SETI Institute'

# The full version, including alpha/beta/rc tags
try:
    release = importlib.metadata.version('rms-host-voyager')
except importlib.metadata.PackageNotFoundError:
    release = '1.0.0'  # fallback for development

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinxcontrib.mermaid',
    'myst_parser',
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# CONTRIBUTING.md is split in contributing.rst; the tail fragment starts at
# "## ..." so MyST reports a false-positive heading-level warning.
suppress_warnings = ['myst.header']

# The suffix(es) of source filenames.
source_suffix = ['.rst', '.md']

# The docstrings wrap variable names in single backticks. Napoleon renders the name of
# each entry in a `Parameters:` block in bold, so the default role must be `strong` for a
# mention of that same name in the surrounding prose to match it. Double backticks mark
# code expressions, and italics mark math symbols that are not variable names, such as
# *x*-axis. An API symbol that should link to its own entry carries an explicit role
# instead.
default_role = 'strong'

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.
html_theme = 'sphinx_rtd_theme'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
# html_static_path = ['_static']

add_module_names = False
autodoc_typehints_format = "short"

# -- Extension configuration -------------------------------------------------

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = True
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# Intersphinx settings
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'filecache': ('https://rms-filecache.readthedocs.io/en/latest/', None),
    'oops': ('https://rms-oops.readthedocs.io/en/latest/', None),
    'pdsparser': ('https://rms-pdsparser.readthedocs.io/en/latest/', None),
    'polymath': ('https://rms-polymath.readthedocs.io/en/latest/', None),
    'vicar': ('https://rms-vicar.readthedocs.io/en/latest/', None),
}

# Nitpicky mode: report every cross-reference that does not resolve. The check script,
# CI, and scripts/read-docs.sh also pass -n, but the ReadTheDocs build takes no extra
# options, so it gets nitpicky mode only from here.
nitpicky = True

# The only cross-references that cannot resolve are the informal type words this
# project's docstrings use to describe what an argument accepts. They name no Python
# object, so there is nothing for Sphinx to link them to. Anything that does name a real
# object is expected to resolve, so do not add entries here for symbols we own or for
# third-party classes with an intersphinx inventory; fix the reference instead.
nitpick_ignore_regex = [
    # Napoleon splits a type such as "(bool, optional)" on the comma and looks up each
    # piece, so the trailing "optional" of every optional parameter arrives here.
    (r'py:class', r'optional'),
    # oops.Host and the oops.host exceptions are not yet in a released oops, so the
    # published oops inventory has no entry for them. Remove these entries once a release
    # that has them is on ReadTheDocs.
    (r'py:class', r'oops\.host\.host_\.Host'),
    (r'py:meth', r'oops\.Host\..*'),
    (r'py:exc', r'oops\.host\..*'),
]

# MyST-Parser settings
myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

# Mermaid settings — use client-side rendering so no mmdc binary is required
# in CI or on ReadTheDocs.
mermaid_output_format = 'raw'

# Generate anchor targets for Markdown headings so intra-document links in
# CONTRIBUTING.md (its table of contents) resolve.
myst_heading_anchors = 2

# The docstrings name the classes of other RMS packages by the short names the code
# imports, or by the oops top-level names, while their inventories record them by module.
# Each short name is rewritten to the target that intersphinx knows before intersphinx
# looks it up.
_SHORT_NAMES = {
    'FCPath'          : 'filecache.file_cache_path.FCPath',
    'Pds3Label'       : 'pdsparser.Pds3Label',
    'VicarLabel'      : 'vicar.VicarLabel',
    'VicarImage'      : 'vicar.VicarImage',
    'Observation'     : 'oops.Observation',
    'Snapshot'        : 'oops.observation.Snapshot',
    'FOV'             : 'oops.FOV',
    'Meshgrid'        : 'oops.Meshgrid',
    'Scalar'          : 'polymath.Scalar',
    'OopsValueError'  : 'oops.OopsValueError',
    'oops.LightSource': 'oops.lightsource.LightSource',
}


def _resolve_short_name(app, env, node, contnode):
    """Resolve a short class name through intersphinx under its full name."""

    if node.get('reftarget') not in _SHORT_NAMES:
        return None
    node['reftarget'] = _SHORT_NAMES[node['reftarget']]
    return missing_reference(app, env, node, contnode)


def setup(app):
    """Run the short-name rewrite ahead of intersphinx's own handler."""

    app.connect('missing-reference', _resolve_short_name, priority=400)
