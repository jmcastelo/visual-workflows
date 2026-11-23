"""
yambo_input.py
==============
A reader of yambo input files that takes them as yambo does: runlevels and
flags (one word on a line), variables (``name= value unit``), blocks
(``% name`` … ``%``), with ``#`` starting a comment, even inside a quoted
string (``parser/PARSER.c``). As in yambo, a block defined twice keeps its
first definition and a variable its last; names are compared without regard
to case (but yambo finds a variable's unit only on a line with the name
spelled as it spells it: see ``docs/yambo.md``).

``yambopy.io.inputfile.YamboIn`` drops string values containing dots, ``<``
or ``/`` and empty ones (``BS_CPU= "2.1.1"``, ``KfnQPdb= "E < ./gw/ndb.QP"``),
turns integers into reals, and switches on runlevels that are commented out
(docs/yambopy-issues.md, issues 4-7).

* :func:`read_yambo_input` — a :class:`YamboInput` from the text of an input.
* :class:`YamboInput` — flags, variables and blocks, looked up without regard
  to case, and changed with :meth:`YamboInput.set_flag`, :meth:`YamboInput.set`,
  :meth:`YamboInput.set_block`, :meth:`YamboInput.unset`.
* :func:`write_yambo_input` — the text of a :class:`YamboInput`, in yambo's
  layout: read back, the same input.

Meant for ``yambopy.io`` (as a reader for ``YamboIn``).
"""

import re
from dataclasses import dataclass, field

__all__ = ['read_yambo_input', 'write_yambo_input', 'YamboInput', 'YamboVariable', 'YamboBlock',
           'YamboInputError']

_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_VARIABLE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
_NUMBER = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$')
_COMPLEX = re.compile(r'^\(\s*([^,()\s]+)\s*,\s*([^,()\s]+)\s*\)(.*)$')
_STUCK_UNIT = re.compile(r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?)([A-Za-z]+)$')


class YamboInputError(ValueError):
    """The text is not a yambo input yambo could read."""


@dataclass
class YamboVariable:
    """A variable: its value (str for strings, int, float or complex for numbers) and
    unit ('' without one), e.g. ``BSENGexx= 30 Ry`` -> (30, 'Ry'). `quoted`:
    whether a string value was quoted (yambo keeps the default for one that
    is not: ``BSKmod= SEX``)."""
    value: object
    unit: str = ''
    quoted: bool = False


@dataclass
class YamboBlock:
    """A block: its rows (lists of values, the columns between ``|``) and unit
    ('' without one), e.g. ``% BSEBands / 20 | 35 | / %`` -> [[20, 35]]."""
    rows: list = field(default_factory=list)
    unit: str = ''


@dataclass
class YamboInput:
    """
    A yambo input as written.

    flags : list of str
        Runlevels and logical flags switched on (``optics``, ``bse``, ``WRbsWF``).
    variables : dict
        {name: YamboVariable}, names as written.
    blocks : dict
        {name: YamboBlock}, names as written.
    """
    flags: list = field(default_factory=list)
    variables: dict = field(default_factory=dict)
    blocks: dict = field(default_factory=dict)
    duplicates: list = field(default_factory=list)   # names given more than once

    def has_flag(self, name):
        """Whether a runlevel or flag is on (case does not matter)."""
        return name.lower() in (flag.lower() for flag in self.flags)

    def variable(self, name):
        """The :class:`YamboVariable` called `name`, whatever its case; None if not set."""
        return _lookup(self.variables, name)

    def get(self, name, default=None):
        """A variable's value, whatever its case; `default` if not set."""
        variable = self.variable(name)
        return default if variable is None else variable.value

    def block(self, name):
        """The :class:`YamboBlock` called `name`, whatever its case; None if not set."""
        return _lookup(self.blocks, name)

    def names(self):
        """Every variable and block name, as written."""
        return list(self.variables) + list(self.blocks)

    # -- changing it ------------------------------------------------------------------

    def set_flag(self, name, on=True):
        """Switch a runlevel or flag on (added at the end) or off."""
        self.flags = [f for f in self.flags if f.lower() != name.lower()]
        if on:
            self.flags.append(_spelling(name))

    def set(self, name, value, unit=None, quoted=None):
        """Set a variable (None unsets it). `unit`: kept from the old value when
        not given; `quoted`: strings are quoted unless said otherwise. The name
        takes yambo's spelling: yambo finds a unit only on a line where the
        name is spelt as it spells it."""
        if value is None:
            self.unset(name)
            return
        old = self.variable(name)
        if unit is None:
            unit = old.unit if old is not None else ''
        if quoted is None:
            quoted = isinstance(value, str)
        self.variables = {k: v for k, v in self.variables.items() if k.lower() != name.lower()}
        self.variables[_spelling(name)] = YamboVariable(value, unit, quoted)

    def set_block(self, name, rows, unit=None):
        """Set a block (None unsets it): rows of values, e.g. [[1, 30]]."""
        if rows is None:
            self.unset(name)
            return
        old = self.block(name)
        if unit is None:
            unit = old.unit if old is not None else ''
        self.blocks = {k: v for k, v in self.blocks.items() if k.lower() != name.lower()}
        self.blocks[_spelling(name)] = YamboBlock([list(r) for r in rows], unit)

    def unset(self, name):
        """Remove a variable, block or flag, whatever its case."""
        lower = name.lower()
        self.variables = {k: v for k, v in self.variables.items() if k.lower() != lower}
        self.blocks = {k: v for k, v in self.blocks.items() if k.lower() != lower}
        self.flags = [f for f in self.flags if f.lower() != lower]


def _spelling(name):
    """yambo's own spelling of a name (as it is when yambo does not know it)."""
    try:
        from .yambo_variables import canonical_name
        return canonical_name(name) or name
    except ImportError:
        return name


def _lookup(table, name):
    lower = name.lower()
    return next((value for key, value in table.items() if key.lower() == lower), None)


def _strip_comment(line):
    """The line without its comment: from '#' on, quoted or not, as yambo reads it."""
    return line.split('#', 1)[0]


def _scalar(word):
    if re.match(r'^[+-]?\d+$', word):
        return int(word)
    if _NUMBER.match(word):
        return float(word.replace('d', 'e').replace('D', 'e'))
    return word


def _value_and_unit(text, where):
    text = text.strip()
    if text[:1] in ('"', "'"):
        end = text.find(text[0], 1)
        if end < 0:
            raise YamboInputError(f'{where}: a string without its closing quote')
        return text[1:end], text[end + 1:].strip(), True
    complex_value = _COMPLEX.match(text)
    if complex_value:                # ( re , im ) unit
        real, imaginary, unit = complex_value.groups()
        return complex(float(real), float(imaginary)), unit.strip(), False
    words = text.split()
    if not words:
        raise YamboInputError(f'{where}: no value')
    stuck = _STUCK_UNIT.match(words[0])
    if stuck:                        # yambo writes "0.000000fs"
        return _scalar(stuck.group(1)), ' '.join([stuck.group(2)] + words[1:]), False
    return _scalar(words[0]), ' '.join(words[1:]), False


def _quoted_part(line):
    """The text after the first quote of a line (where a '#' would cut a string)."""
    quotes = [i for i in (line.find('"'), line.find("'")) if i >= 0]
    return line[min(quotes) + 1:] if quotes else ''


def read_yambo_input(text):
    """
    Read a yambo input.

    Parameters
    ----------
    text : str
        The input file's contents.

    Returns
    -------
    YamboInput

    Raises
    ------
    YamboInputError
        A block not closed by ``%``, a line yambo would not understand.
    """
    result = YamboInput()
    block_name, block = None, None
    for number, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        where = f'line {number}'
        if block is not None:
            if line == '%':
                if _lookup(result.blocks, block_name) is None:    # the first one counts
                    result.blocks[block_name] = block
                else:
                    result.duplicates.append(block_name)
                block_name, block = None, None
                continue
            cells = [cell.strip() for cell in line.split('|')]
            values, unit = cells[:-1], cells[-1]     # after the last '|': a unit, or nothing
            if not values:                            # a row without '|': values separated by spaces
                values, unit = line.split(), ''
            block.rows.append([_value_and_unit(v, where)[0] if v[:1] in ('"', "'") else _scalar(v)
                               for v in values if v != ''])
            if unit:
                block.unit = unit
            continue
        if line.startswith('%'):
            name = line[1:].strip()
            if not _NAME.match(name):
                raise YamboInputError(f'{where}: a block without a name')
            block_name, block = name, YamboBlock()
            continue
        m = _VARIABLE.match(line)
        if m:
            try:
                value, unit, quoted = _value_and_unit(m.group(2), f'{where} ({m.group(1)})')
            except YamboInputError as e:
                if '#' in _quoted_part(raw):
                    raise YamboInputError(f"{e}: the '#' in it starts a comment for yambo") from None
                raise
            previous = _lookup(result.variables, m.group(1))
            if previous is not None:
                result.duplicates.append(m.group(1))
                result.variables = {k: v for k, v in result.variables.items()
                                    if k.lower() != m.group(1).lower()}
            result.variables[m.group(1)] = YamboVariable(value, unit, quoted)   # the last counts
            continue
        if _NAME.match(line):
            result.flags.append(line)
            continue
        raise YamboInputError(f'{where}: not a runlevel, a variable or a block ({line!r})')
    if block is not None:
        raise YamboInputError(f'block {block_name} is not closed by "%"')
    return result


# -- writing --------------------------------------------------------------------------------

_WIDTH = 32                          # where yambo puts its comments


def write_yambo_input(inp, order=(), describe=None):
    """
    The text of a yambo input: runlevels and flags, then variables and blocks,
    as yambo lays them out (``name= value unit``, ``% name`` … ``%``).

    Parameters
    ----------
    inp : YamboInput
    order : iterable of str
        Names in the order to write them (as yambo writes them:
        ``yambo_runlevels.input_names``); the others follow, as they are in
        `inp`. Flags and runlevels come first either way.
    describe : callable, optional
        name -> a description written as a comment after it (yambo's own), or ''.

    Returns
    -------
    str
        Read back with :func:`read_yambo_input`, the same input.

    Raises
    ------
    YamboInputError
        A value yambo would read otherwise: a string with ``#`` (a comment for
        yambo) or with both kinds of quotes, an unquoted string that is not
        one word, a number that is not finite.
    """
    rank = {name.lower(): i for i, name in enumerate(order)}
    key = lambda name: rank.get(name.lower(), len(rank))        # noqa: E731

    def comment(name, line):
        text = describe(name) if describe else ''
        if not text:
            return line
        return f"{line:<{_WIDTH}} # {text}" if len(line) < _WIDTH else f"{line}   # {text}"

    lines = [comment(f, f) for f in sorted(inp.flags, key=key)]
    entries = [(n, 'v') for n in inp.variables] + [(n, 'b') for n in inp.blocks]
    for name, kind in sorted(entries, key=lambda e: key(e[0])):
        if kind == 'v':
            v = inp.variables[name]
            text = f"{name}= {_value(v.value, v.quoted, name)}"
            lines.append(comment(name, f"{text} {v.unit}".rstrip()))
        else:
            b = inp.blocks[name]
            lines.append(comment(name, f"% {name}"))
            for row in b.rows:
                cells = ' | '.join(_value(x, isinstance(x, str), name) for x in row)
                lines.append(f" {cells} |" + (f" {b.unit}" if b.unit else ''))
            lines.append('%')
    return '\n'.join(lines) + '\n'


def _value(value, quoted, name):
    if isinstance(value, bool):
        raise YamboInputError(f'{name}: yambo has no logical values (a flag is on or off)')
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            raise YamboInputError(f'{name}: {value} is not a number yambo reads')
        return repr(value)
    if isinstance(value, complex):
        return f'( {_value(value.real, False, name)} , {_value(value.imag, False, name)} )'
    text = str(value)
    if '#' in text:
        raise YamboInputError(f'{name}: {text!r} has a "#", where yambo would cut it (a comment)')
    if not quoted:
        if not text or len(text.split()) != 1 or text[0] in '"\'' or _scalar(text) != text:
            raise YamboInputError(f'{name}: {text!r} cannot be written unquoted')
        return text
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    raise YamboInputError(f'{name}: a string with both kinds of quotes')
