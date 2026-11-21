"""
yambo_input.py
==============
A reader of yambo input files that takes them as yambo does: runlevels and
flags (one word on a line), variables (``name= value unit``), blocks
(``% name`` … ``%``), with ``#`` starting a comment.

``yambopy.io.inputfile.YamboIn`` drops string values containing dots, ``<``
or ``/`` and empty ones (``BS_CPU= "2.1.1"``, ``KfnQPdb= "E < ./gw/ndb.QP"``),
turns integers into reals, and switches on runlevels that are commented out
(docs/yambopy-issues.md, issues 4-7).

* :func:`read_yambo_input` — a :class:`YamboInput` from the text of an input.
* :class:`YamboInput` — flags, variables and blocks, looked up without regard
  to case.

Meant for ``yambopy.io`` (as a reader for ``YamboIn``).
"""

import re
from dataclasses import dataclass, field

__all__ = ['read_yambo_input', 'YamboInput', 'YamboVariable', 'YamboBlock', 'YamboInputError']

_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_VARIABLE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
_NUMBER = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$')


class YamboInputError(ValueError):
    """The text is not a yambo input yambo could read."""


@dataclass
class YamboVariable:
    """A variable: its value (str for strings, int or float for numbers) and
    unit ('' without one), e.g. ``BSENGexx= 30 Ry`` -> (30, 'Ry')."""
    value: object
    unit: str = ''


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


def _lookup(table, name):
    lower = name.lower()
    return next((value for key, value in table.items() if key.lower() == lower), None)


def _strip_comment(line):
    """The line without its comment: '#' outside double quotes."""
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == '#' and not quoted:
            return line[:i]
    return line


def _scalar(word):
    if re.match(r'^[+-]?\d+$', word):
        return int(word)
    if _NUMBER.match(word):
        return float(word.replace('d', 'e').replace('D', 'e'))
    return word


def _value_and_unit(text, where):
    text = text.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            raise YamboInputError(f'{where}: a string without its closing quote')
        return text[1:end], text[end + 1:].strip()
    words = text.split()
    if not words:
        raise YamboInputError(f'{where}: no value')
    return _scalar(words[0]), ' '.join(words[1:])


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
                result.blocks[block_name] = block
                block_name, block = None, None
                continue
            cells = [cell.strip() for cell in line.split('|')]
            values, unit = cells[:-1], cells[-1]     # after the last '|': a unit, or nothing
            if not values:                            # a row without '|': values separated by spaces
                values, unit = line.split(), ''
            block.rows.append([_value_and_unit(v, where)[0] if v.startswith('"') else _scalar(v)
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
            value, unit = _value_and_unit(m.group(2), f'{where} ({m.group(1)})')
            result.variables[m.group(1)] = YamboVariable(value, unit)
            continue
        if _NAME.match(line):
            result.flags.append(line)
            continue
        raise YamboInputError(f'{where}: not a runlevel, a variable or a block ({line!r})')
    if block is not None:
        raise YamboInputError(f'block {block_name} is not closed by "%"')
    return result
