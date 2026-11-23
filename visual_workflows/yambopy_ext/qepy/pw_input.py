"""
pw_input.py
===========
A reader of pw.x input files that takes them as pw.x does: the namelists'
values (strings containing ``/``, logicals, Fortran reals, array elements),
then the cards, with nothing added.

``yambopy.qepy.PwIn`` cuts string values at ``/`` (``pseudo_dir='../pseudos/'``
becomes ``'..``), raises ``StopIteration`` on ``K_POINTS gamma`` and merges its
own defaults into the file it reads (docs/yambopy-issues.md, issues 1-3).

* :func:`read_pw_input` — a :class:`PwInput` from the text of an input file.
* :class:`PwInput` — namelists and cards, with :meth:`PwInput.k_points`, and
  :meth:`PwInput.set`, :meth:`PwInput.unset`, :meth:`PwInput.set_card` to change them.
* :func:`write_pw_input` — the text of a :class:`PwInput`: read back, the same
  input.

Meant for ``yambopy.qepy`` (as a reader for ``PwIn``). Names are lowercase
(``ecutwfc``, ``starting_magnetization(1)``); card names uppercase.
"""

import re
from dataclasses import dataclass, field

__all__ = ['read_pw_input', 'write_pw_input', 'PwInput', 'Card', 'KPoints', 'PwInputError',
           'NAMELIST_ORDER', 'SPECIES_ARRAYS']

# Cards pw.x knows (INPUT_PW): a line starting with one of these starts a card
CARDS = ('ATOMIC_SPECIES', 'ATOMIC_POSITIONS', 'K_POINTS', 'ADDITIONAL_K_POINTS',
         'CELL_PARAMETERS', 'OCCUPATIONS', 'CONSTRAINTS', 'ATOMIC_VELOCITIES',
         'ATOMIC_FORCES', 'SOLVENTS', 'HUBBARD')

_TOKEN = re.compile(r"""
    (?P<string>'[^']*'|"[^"]*")      # quoted strings, '/' and '!' included
  | (?P<comment>![^\n]*)              # comments, to the end of the line
  | (?P<end>/)                        # the end of a namelist
  | (?P<eq>=)
  | (?P<comma>,)
  | (?P<word>[^\s,=/'"!]+)            # names, numbers, logicals
  | (?P<space>\s+)
""", re.X)
# The order pw.x reads its namelists in (Modules/read_namelists.f90)
NAMELIST_ORDER = ('control', 'system', 'electrons', 'ions', 'cell', 'press_ai', 'wannier',
                  'wannier_ac', 'fcp', 'rism')

# Variables with an element per species (INPUT_PW, qe-7.5: dimension ... ntyp), and
# which of their indices is the species: renumbered when the species change
SPECIES_ARRAYS = {'starting_charge': 0, 'starting_magnetization': 0, 'hubbard_occ': 0,
                  'hubbard_beta': 0, 'starting_ns_eigenvalue': 2, 'angle1': 0, 'angle2': 0,
                  'london_c6': 0, 'london_rvdw': 0, 'nhgrp': 0, 'fnhscl': 0, 'solute_lj': 0,
                  'solute_epsilon': 0, 'solute_sigma': 0}

_LOGICAL = re.compile(r'^\.?(t|true|f|false)\.?$', re.I)
_INTEGER = re.compile(r'^[+-]?\d+$')
_REAL = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eEdDqQ][+-]?\d+)?$')


class PwInputError(ValueError):
    """The text is not a pw.x input pw.x could read."""


@dataclass
class Card:
    """A card: its option (``automatic`` in ``K_POINTS {automatic}``, lowercase;
    '' without one) and its lines, without comments or empty lines.

    pw.x skips only blank lines and lines with ``#`` in the first column inside a
    card: a whole-line ``!`` comment, or ``#`` after spaces, is read as data.
    Such lines are not in `lines` but in `misread`, for checks to report."""
    option: str = ''
    lines: list = field(default_factory=list)
    misread: list = field(default_factory=list)


@dataclass
class KPoints:
    """
    The k-points a ``K_POINTS`` card gives.

    kind : 'automatic', 'gamma' or 'list'
    grid : (n1, n2, n3) for 'automatic'
    shift : (s1, s2, s3) for 'automatic'
    count : the number of k-points: 1 for 'gamma', the list's for 'list', None
        for 'automatic' (symmetry reduces the grid; see :attr:`upper_bound`)
        and for band paths (``tpiba_b``, ``crystal_b``)
    """
    kind: str
    grid: tuple = None
    shift: tuple = None
    count: int = None

    @property
    def upper_bound(self):
        """At most this many k-points (the grid's product for 'automatic')."""
        if self.count is not None:
            return self.count
        if self.grid is not None:
            n1, n2, n3 = self.grid
            return n1 * n2 * n3
        return None


@dataclass
class PwInput:
    """
    A pw.x input as written.

    namelists : {'control': {'calculation': 'scf', ...}, 'system': {...}, ...}
        namelist names lowercase, without '&'; values as str, int, float or bool
    cards : {'K_POINTS': Card('automatic', ['4 4 1 0 0 0']), ...}
        in the file's order
    namelist_order : list
        The namelists in the file's order (pw.x reads them in a fixed order)
    """
    namelists: dict = field(default_factory=dict)
    cards: dict = field(default_factory=dict)
    namelist_order: list = field(default_factory=list)

    def get(self, namelist, name, default=None):
        """A namelist value, e.g. ``get('system', 'nbnd')``; `default` if not set."""
        return self.namelists.get(namelist.lower().lstrip('&'), {}).get(name.lower(), default)

    def set(self, namelist, name, value):
        """Set a namelist value (adding the namelist if needed); None unsets it."""
        namelist, name = namelist.lower().lstrip('&'), re.sub(r'\s+', '', name.lower())
        if value is None:
            self.unset(namelist, name)
            return
        if namelist not in self.namelists:
            self.namelists[namelist] = {}
            self.namelist_order.append(namelist)
        self.namelists[namelist][name] = value

    def unset(self, namelist, name):
        """Remove a namelist value, if there."""
        self.namelists.get(namelist.lower().lstrip('&'), {}).pop(re.sub(r'\s+', '', name.lower()), None)

    def set_card(self, name, option='', lines=()):
        """Set a card (replacing it); lines without the card's header."""
        name = name.upper()
        if name not in CARDS:
            raise PwInputError(f'{name}: not a card pw.x knows')
        self.cards[name] = Card(option.lower(), [str(line).strip() for line in lines])

    def remove_card(self, name):
        """Remove a card, if there."""
        self.cards.pop(name.upper(), None)

    def k_points(self):
        """The :class:`KPoints` of the ``K_POINTS`` card (None without one)."""
        card = self.cards.get('K_POINTS')
        if card is None:
            return None
        option = card.option or 'tpiba'
        if option == 'gamma':
            return KPoints('gamma', count=1)
        if option == 'automatic':
            try:
                numbers = [int(x) for x in card.lines[0].split()[:6]]
            except (IndexError, ValueError):
                raise PwInputError('K_POINTS automatic: the grid line is not 6 integers') from None
            if len(numbers) != 6:
                raise PwInputError('K_POINTS automatic: the grid line is not 6 integers')
            return KPoints('automatic', grid=tuple(numbers[:3]), shift=tuple(numbers[3:]))
        if option.endswith('_b'):
            return KPoints('list')                   # a path: as many points as its weights add
        try:
            return KPoints('list', count=int(card.lines[0].split()[0]))
        except (IndexError, ValueError):
            raise PwInputError(f'K_POINTS {option}: the first line is not the number of k-points') from None


def _value(word):
    if _LOGICAL.match(word):
        return word.strip('.').lower() in ('t', 'true')
    if _INTEGER.match(word):
        return int(word)
    if _REAL.match(word):
        return float(re.sub('[dDqQ]', 'e', word))
    return word                      # not valid Fortran: kept as written


def read_pw_input(text):
    """
    Read a pw.x input.

    Parameters
    ----------
    text : str
        The input file's contents.

    Returns
    -------
    PwInput

    Raises
    ------
    PwInputError
        A namelist not closed by ``/``, a value without a name, and the like.
    """
    namelists, order, position = {}, [], 0
    while True:
        # Fortran skips whatever precedes a namelist (comments, '#' headers)
        start = re.compile(r'(?:[^&\n]*\n)*?\s*&(\w+)', re.A).match(text, position)
        if not start:
            break
        name = start.group(1).lower()
        values, position = _read_namelist(text, start.end(), name)
        namelists.setdefault(name, {}).update(values)
        order.append(name)
    return PwInput(namelists, _read_cards(text[position:]), order)


def _read_namelist(text, position, name):
    """The values of a namelist, and where its closing '/' ends."""
    values, key, pending = {}, None, []
    for kind, token, end in _tokens(text, position):
        if kind == 'end':
            _store(values, key, pending, name)
            # Fortran ignores the rest of the record after '/' (as in '/&end')
            newline = text.find('\n', end)
            return values, (len(text) if newline < 0 else newline + 1)
        if kind == 'eq':
            if not pending:
                raise PwInputError(f'&{name}: "=" without a name')
            # The word before '=' is the next name; what came before it, values
            new_key = pending.pop()
            if isinstance(new_key, _Quoted):
                raise PwInputError(f'&{name}: "=" after a value')
            _store(values, key, pending, name)
            key, pending = new_key.lower(), []
            continue
        pending.append(_Quoted(token[1:-1]) if kind == 'string' else token)
    raise PwInputError(f'&{name} is not closed by "/"')


def _tokens(text, position):
    """(kind, text, end) of the namelist's tokens from `position`: 'string',
    'word', 'eq', 'end'; array elements with commas (``a(1,2)``) one word."""
    word, depth = '', 0
    for m in _TOKEN.finditer(text, position):
        kind = m.lastgroup
        if depth:                    # inside the parentheses of an element
            if kind in ('word', 'comma'):
                word += m.group()
                depth += m.group().count('(') - m.group().count(')')
                if depth <= 0:
                    depth = 0
                    yield 'word', word, m.end()
            continue
        if kind in ('space', 'comment', 'comma'):
            continue
        if kind == 'word' and m.group().count('(') > m.group().count(')'):
            word, depth = m.group(), m.group().count('(') - m.group().count(')')
            continue
        yield kind, m.group(), m.end()


class _Quoted(str):
    """A string value (quoted in the file): never a name, never converted."""


def _store(values, key, pending, name):
    if key is None:
        if pending:
            raise PwInputError(f'&{name}: a value without a name ({pending[0]})')
        return
    if not pending:
        raise PwInputError(f'&{name}: {key} has no value')
    converted = [str(v) if isinstance(v, _Quoted) else _value(v) for v in pending]
    values[re.sub(r'\s+', '', key)] = converted[0] if len(converted) == 1 else converted


def _read_cards(text):
    cards, current = {}, None
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith('#'):
            continue                 # what pw.x skips (parser.f90, read_line)
        if raw.strip()[0] in '!#':
            if current is not None:
                current.misread.append(raw.strip())
            continue
        line = re.split(r'[!#]', raw, maxsplit=1)[0].strip()
        words = line.split(None, 1)
        head = words[0].upper()
        if head in CARDS:
            option = words[1].strip(' {}()').lower() if len(words) > 1 else ''
            current = cards[head] = Card(option)
        elif current is not None:
            current.lines.append(line)
    return cards


def write_pw_input(pw):
    """
    The text of a pw.x input: namelists in the order pw.x reads them, each
    variable in the order it has in `pw`, then the cards in INPUT_PW's order.

    Reading the text back gives the same namelists and cards. Lines pw.x
    would misread inside a card (`Card.misread`) are not written.

    Raises
    ------
    PwInputError
        A value pw.x cannot be given: a string with both kinds of quotes, a
        real that is not finite, an empty list.
    """
    names = [n for n in NAMELIST_ORDER if n in pw.namelists]
    names += [n for n in dict.fromkeys(pw.namelist_order) if n in pw.namelists and n not in names]
    lines = []
    for namelist in names:
        values = pw.namelists[namelist]
        lines.append(f'&{namelist}')
        width = max((len(k) for k in values), default=0)
        for key, value in values.items():
            lines.append(f'    {key:<{width}} = {_fortran(value, key)}')
        lines.append('/')
    for name in CARDS:
        card = pw.cards.get(name)
        if card is None:
            continue
        lines.append(f'{name} {card.option}'.rstrip())
        lines.extend(f'  {line}' for line in card.lines)
    return '\n'.join(lines) + '\n'


def _fortran(value, key=''):
    if isinstance(value, (list, tuple)):
        if not value:
            raise PwInputError(f'{key}: no value')
        return ', '.join(_fortran(v, key) for v in value)
    if isinstance(value, bool):
        return '.true.' if value else '.false.'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            raise PwInputError(f'{key}: {value} is not a number pw.x reads')
        return repr(value)
    text = str(value)
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    raise PwInputError(f'{key}: a string with both kinds of quotes')
