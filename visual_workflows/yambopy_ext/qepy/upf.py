"""
upf.py
======
The header of a pseudopotential file in UPF, as pw.x reads it
(``upflib/read_upf_new.f90`` for UPF 2 and the schema form,
``upflib/read_upf_v1.f90`` for UPF 1): what a pw.x or yambo input needs to be
checked against (valence charge, norm-conserving or not, spin–orbit,
functional, suggested cutoffs).

Only the header is read: files are hundreds of kilobytes, the header is near
the top, so the first :data:`HEADER_BYTES` are enough (for UPF 1, spin–orbit
is in ``<PP_ADDINFO>`` at the end: unknown unless the whole text is given).

* :func:`read_upf_header` — a :class:`UpfHeader` from a file's text (or its start).

Meant for ``yambopy.qepy``.
"""

import re
from dataclasses import dataclass

__all__ = ['read_upf_header', 'UpfHeader', 'UpfError', 'HEADER_BYTES']

# How much of a file to read for its header (UPF 2 files put PP_INFO, with the
# generator's input, before it)
HEADER_BYTES = 64 * 1024

_TRUE = ('t', 'true', '.true.', 'yes', 'y', '1')


class UpfError(ValueError):
    """The text is not a UPF file pw.x could read (or its header is not in it)."""


@dataclass
class UpfHeader:
    """
    A UPF header.

    element : str
    pseudo_type : str
        'NC', 'SL' (norm-conserving), 'US', 'PAW', '1/r'...
    z_valence : float
    is_ultrasoft, is_paw : bool
    has_so : bool or None
        Spin–orbit (fully relativistic); None when not known (UPF 1 read in part)
    relativistic : str
        'scalar', 'full', ... ('' for UPF 1)
    functional : str
        As written (``PBE``, ``SLA PW PBX PBC``)
    core_correction : bool
    wfc_cutoff, rho_cutoff : float
        Suggested cutoffs in Ry (0: none)
    version : int
        1 or 2 (the schema form counts as 2)
    """
    element: str
    pseudo_type: str
    z_valence: float
    is_ultrasoft: bool = False
    is_paw: bool = False
    has_so: object = False
    relativistic: str = ''
    functional: str = ''
    core_correction: bool = False
    wfc_cutoff: float = 0.0
    rho_cutoff: float = 0.0
    version: int = 2

    @property
    def norm_conserving(self):
        """Neither ultrasoft nor PAW (what yambo's p2y takes)."""
        return not (self.is_ultrasoft or self.is_paw)


def _bool(text):
    return str(text).strip().lower() in _TRUE


def _float(text):
    try:
        return float(str(text).strip().lower().replace('d', 'e'))
    except ValueError:
        raise UpfError(f'not a number: {text!r}') from None


def read_upf_header(text):
    """
    Read the header of a UPF file.

    Parameters
    ----------
    text : str
        The file's contents, or its first :data:`HEADER_BYTES` at least.

    Returns
    -------
    UpfHeader

    Raises
    ------
    UpfError
        No header found, or one pw.x would not read.
    """
    m = re.search(r'<PP_HEADER\b([^>]*?)/>', text, re.S | re.I)
    if m and '=' in m.group(1):
        return _from_attributes(dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1))))
    m = re.search(r'<PP_HEADER\b[^>]*>(.*?)</PP_HEADER>', text, re.S | re.I)
    if m is None:
        raise UpfError('no PP_HEADER (not a UPF file, or its header is beyond what was read)')
    body = m.group(1)
    tags = dict((k.lower(), v.strip()) for k, v in re.findall(r'<(\w+)>([^<]*)</\1>', body))
    if tags:                         # the schema form: one element per field
        return _from_attributes(tags)
    return _from_v1(body, text)


def _from_attributes(a):
    a = {k.lower(): v for k, v in a.items()}
    for key in ('element', 'z_valence'):
        if key not in a:
            raise UpfError(f'PP_HEADER without {key}')
    pseudo_type = a.get('pseudo_type', a.get('type', '')).strip()
    return UpfHeader(
        element=a['element'].strip(), pseudo_type=pseudo_type,
        z_valence=_float(a['z_valence']),
        is_ultrasoft=_bool(a.get('is_ultrasoft', 'F')), is_paw=_bool(a.get('is_paw', 'F')),
        has_so=_bool(a.get('has_so', 'F')), relativistic=a.get('relativistic', '').strip(),
        functional=' '.join(a.get('functional', '').split()),
        core_correction=_bool(a.get('core_correction', 'F')),
        wfc_cutoff=_float(a.get('wfc_cutoff', '0')), rho_cutoff=_float(a.get('rho_cutoff', '0')))


def _from_v1(body, text):
    """UPF 1: one field per line, in a fixed order (read_pseudo_header)."""
    lines = [line for line in body.splitlines() if line.strip()]
    try:
        element = lines[1].split()[0]
        kind = lines[2].strip()
        nlcc = lines[3].split()[0]
        functional = ' '.join(lines[4][:20].split())
        z_valence = _float(lines[5].split()[0])
        cutoffs = lines[7].split()
    except IndexError:
        raise UpfError('UPF 1 header cut short') from None
    # pw.x matches the type as a substring, in this order
    if 'US' in kind:
        ultrasoft, paw, pseudo_type = True, False, 'US'
    elif 'PAW' in kind:
        ultrasoft, paw, pseudo_type = True, True, 'PAW'
    elif 'NC' in kind:
        ultrasoft, paw, pseudo_type = False, False, 'NC'
    elif '1/r' in kind:
        ultrasoft, paw, pseudo_type = False, False, '1/r'
    else:
        raise UpfError(f'unknown pseudo type {kind.split()[0] if kind else kind!r} '
                       '(pw.x stops: read_pseudo_header)')
    # Spin–orbit data (PP_ADDINFO) come after PP_NONLOCAL: known only if read that far
    has_so = ('<PP_ADDINFO>' in text) if '</PP_NONLOCAL>' in text else None
    return UpfHeader(
        element=element, pseudo_type=pseudo_type, z_valence=z_valence, is_ultrasoft=ultrasoft,
        is_paw=paw, has_so=has_so, relativistic='', functional=functional,
        core_correction=_bool(nlcc),
        wfc_cutoff=_float(cutoffs[0]) if cutoffs else 0.0,
        rho_cutoff=_float(cutoffs[1]) if len(cutoffs) > 1 else 0.0, version=1)
