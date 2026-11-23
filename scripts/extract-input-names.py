#!/usr/bin/env python3
"""Regenerate the lists of input names per version, from clones of QE and Yambo:

    visual_workflows/yambopy_ext/qepy/pw_namelists.py     (pw.x namelists)
    visual_workflows/yambopy_ext/io/yambo_variables.py    (yambo names, and the
                                                          command-line options)
    visual_workflows/yambopy_ext/io/yambo_runlevels.py    (what each runlevel brings
                                                          into yambo's input)

Usage: scripts/extract-input-names.py QE_REPO YAMBO_REPO
       [--qe qe-7.3.1 qe-7.5 qe-7.6] [--yambo 5.1.1 5.3.0 5.4-beta]

Tags are read with `git show`: the clones are not checked out or changed.
Each module's docstring says how its lists are made; this script does that
and writes the data part (the rest of each module is kept).
"""

import argparse
import json
import pprint
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QE_MODULE = ROOT / "visual_workflows/yambopy_ext/qepy/pw_namelists.py"
YAMBO_MODULE = ROOT / "visual_workflows/yambopy_ext/io/yambo_variables.py"
RUNLEVELS_MODULE = ROOT / "visual_workflows/yambopy_ext/io/yambo_runlevels.py"


def show(repo, tag, path):
    r = subprocess.run(["git", "-C", repo, "show", f"{tag}:{path}"], capture_output=True, text=True)
    return r.stdout


def strip_comments(text):
    """Fortran free form: '!' outside strings ends the line; continuations joined."""
    lines = []
    for line in text.splitlines():
        out, quote = "", None
        for ch in line:
            if quote:
                out += ch
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
                out += ch
            elif ch == "!":
                break
            else:
                out += ch
        lines.append(out.rstrip())
    return re.sub(r"&\s*\n\s*&?", " ", "\n".join(lines))


# -- QE ---------------------------------------------------------------------------------

def qe_namelists(repo, tag):
    text = strip_comments(show(repo, tag, "Modules/input_parameters.f90"))
    names = {}
    for m in re.finditer(r"NAMELIST\s*/\s*(\w+)\s*/([^\n]*)", text, re.I):
        names.setdefault(m.group(1).lower(), []).extend(
            w.strip().lower() for w in m.group(2).split(",") if w.strip())
    return {k: sorted(set(v)) for k, v in names.items()}


# -- Yambo ------------------------------------------------------------------------------

YAMBO_FILES = ["src/interface/INIT_load.F", "src/interface/INIT_QP_ctl_load.F",
               "src/interface/INIT_RT_ctl_load.F", "src/interface/INIT_C_driver_runlevels.F"]


def call_args(text):
    out, depth, cur, quote = [], 0, "", None
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            cur += ch
            continue
        if ch == "(":
            depth += 1
        if ch == ")":
            if depth == 0:
                out.append(cur.strip())
                return out
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    out.append(cur.strip())
    return out


def yambo_names(repo, tag):
    literal, built, units = {}, [], {}
    for path in YAMBO_FILES:
        for m in re.finditer(r"call\s+it\s*\((.*)", strip_comments(show(repo, tag, path)), re.I):
            a = call_args(m.group(1))
            if len(a) < 2:
                continue
            if re.fullmatch(r"'[rf]'", a[0]):
                kind, name = ("runlevel" if a[0] == "'r'" else "flag"), a[2]
            else:
                kind, name = "variable", a[1]
            lit = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", name)
            unit = next((m.group(1) for x in a
                         for m in [re.fullmatch(r"(?:unit\s*=\s*)?(\w+_unit)", x)] if m), None)
            if lit:
                literal[(lit.group(1) or lit.group(2)).strip()] = kind
                if unit:
                    units[(lit.group(1) or lit.group(2)).strip()] = unit
            elif unit and name.startswith("nms("):
                units[name] = unit
            else:
                built.append((path.split("/")[-1], name))
    runlevels = {n for n, k in literal.items() if k == "runlevel"}
    flags = {n for n, k in literal.items() if k == "flag"}
    variables = {n for n, k in literal.items() if k == "variable"}
    files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", tag],
                           capture_output=True, text=True).stdout.split()
    for path in files:
        if re.search(r"driver/options_(yambo|projects|control)\.c$", path):
            runlevels |= set(re.findall(r'yambo_string="([^"]+)"', show(repo, tag, path))) - {"undef"}
    load = show(repo, tag, "src/interface/INIT_load.F")
    stems = re.findall(r"'(\w+)'", re.search(r"nms=\(/(.*?)/\)",
                                             re.sub(r"&\s*\n\s*&?", " ", load), re.S).group(1))
    suffixes = set(re.findall(r"sfx='(\w+)'", load))
    variables |= {s[:6] + x for s in stems for x in suffixes}
    for key, unit in list(units.items()):
        if key.startswith("nms("):
            stem = stems[int(re.search(r"\d+", key).group()) - 1]
            units.update({stem[:6] + x: unit for x in suffixes})
            del units[key]
    sections = re.findall(r'Short_Description="(\w+)"', show(repo, tag, "src/modules/mod_parallel.F"))
    for s in sections:
        variables |= {f"{s}_CPU", f"{s}_ROLEs", f"{s}_nCPU_LinAlg_INV", f"{s}_nCPU_LinAlg_DIAGO"}
    for path in ("src/interface/INIT_QP_ctl_load.F", "src/interface/INIT_RT_ctl_load.F"):
        text = show(repo, tag, path)
        prefixes = re.findall(r"'(\w)'", re.search(r"prefix=\(/(.*?)/\)", text).group(1))
        spin = re.search(r"spin_ch=\(/(.*?)/\)", text)
        spins = [s.strip() for s in re.findall(r"'([^']*)'", spin.group(1))] if spin else [""]
        for _, b in [x for x in built if x[0] == path.split("/")[-1]]:
            parts = re.findall(r"'([^']*)'", b)
            for p in prefixes:
                if "spin_ch" in b:
                    variables |= {p + parts[0] + sp + parts[1] for sp in spins}
                else:
                    variables.add(p + "".join(parts))
    grep = subprocess.run(["git", "-C", repo, "grep", "-hoE",
                           r"""PARSER_array\([^)]*VAR=["'][A-Za-z_0-9]+["']|call +parser\( *["'][A-Za-z_0-9]+["']""",
                           tag, "--", "src/*"], capture_output=True, text=True).stdout
    for name in re.findall(r"""["']([A-Za-z_0-9]+)["']\s*$""", grep, re.M):
        if name not in runlevels and name not in flags:
            variables.add(name)
    unresolved = [b for f, b in built if f == "INIT_load.F" and "trim(name)" not in b
                  and not b.startswith("nms(")]
    if unresolved:
        sys.exit(f"{tag}: names built in a way this script does not know: {unresolved}")
    return {"runlevels": sorted(runlevels), "flags": sorted(flags), "variables": sorted(variables),
            "patterns": [r"Field\d+_(Freq|NFreqs|DFreq|Int|Width|FWHM|kind|pol|Dir|Dir_circ|Tstart)"],
            "sections": sections,
            # The kind of unit each variable takes (E_unit: an energy, read in Ha
            # without one; G_unit: G vectors, a count without one)
            "units": {k: units[k] for k in sorted(units)}}


# Strings an option may set without making yambo edit its input instead of
# running (INIT_read_command_line.F: strings_to_not_use_as_runlevels)
NOT_RUNLEVELS = {"jobstr", "ifile", "idir", "odir", "cdir", "com_path", "more_io_path",
                 "core_io_path", "wallt", "memorylimit"}
EXECUTABLES = ("yambo", "ypp", "p2y")


def yambo_options(repo, tag):
    """{executable: [{short, long, arg, kind}]}: kind "job" (fine in a job),
    "editing" (yambo writes the input file and exits), "exit" (prints and exits)."""
    files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", tag],
                           capture_output=True, text=True).stdout.split()
    result = {exe: [] for exe in EXECUTABLES}
    for path in files:
        if not re.search(r"driver/options_\w+\.c$", path):
            continue
        for block in re.split(r"\*i_opt=\*i_opt\+1;", show(repo, tag, path))[1:]:
            o = {k: v.strip().strip("\"'") for k, v in
                 re.findall(r"options\[\*i_opt\]\.(\w+)=([^;]+);", block)}
            if "short_opt" not in o and "long_opt" not in o:
                continue
            bins = o.get("bin", "all").split()      # options_maker.c: "all" by default
            arg = ("int" if o.get("int_var") else "char" if o.get("char_var")
                   else "float" if o.get("float_var") else "")
            string = o.get("yambo_string", "")
            for exe in EXECUTABLES:
                if exe not in bins and "all" not in bins:
                    continue
                if o.get("long_opt") in ("help", "version"):
                    kind = "exit"
                elif string and string not in NOT_RUNLEVELS and exe in bins and exe != "p2y":
                    kind = "editing"     # a runlevel of this executable: input editing
                    # (p2y has no input file: its strings are its own switches)
                else:
                    kind = "job"
                entry = {"short": o.get("short_opt", ""), "long": o.get("long_opt", ""),
                         "arg": arg, "kind": kind}
                if entry not in result[exe]:
                    result[exe].append(entry)
    return result


# -- what each runlevel brings (INIT_activate.F, INIT.F) ------------------------------------

# Compile-time switches of the plain `yambo` executable: the others belong to
# yambo_rt, yambo_sc, yambo_nl, yambo_ph... (_MPI and _SCALAPACK depend on the
# build: they stay in the conditions, as the variables MPI and SCALAPACK)
YAMBO_DEFINES_OFF = {"_RT", "_SC", "_NL", "_ELPH", "_PHEL", "_QED", "_MODELS", "_RT_SCATT",
                     "_MAGNETIC", "_MEM_CHECK", "_PAR_IO", "_DOUBLE", "_GPU", "_CUDA", "_OPENACC",
                     "_OPENMP_GPU", "_HIP", "_SLEPC", "_KERR", "_PL", "_ELECTRIC", "_YPP_RT",
                     "_YPP_SC", "_YPP_NL", "_YPP_ELPH", "_YAML_OUTPUT", "_TIMING", "_NC_OUTPUT"}
YAMBO_DEFINES_RUNTIME = {"_MPI": "MPI", "_SCALAPACK": "SCALAPACK"}


def _preprocessor(cond):
    """A #if condition: True/False when known for plain yambo, else an expression."""
    cond = cond.strip()
    parts = re.split(r"(\|\||&&)", cond)
    out = []
    for p in parts:
        if p in ("||", "&&"):
            out.append(" or " if p == "||" else " and ")
            continue
        m = re.fullmatch(r"\s*(!)?\s*defined\s*\(?\s*(\w+)\s*\)?\s*", p)
        if not m:
            raise ValueError(f"#if {cond!r}: not understood")
        negate, name = m.groups()
        if name in YAMBO_DEFINES_RUNTIME:
            term = YAMBO_DEFINES_RUNTIME[name]
        else:
            term = "False" if name in YAMBO_DEFINES_OFF or name not in YAMBO_DEFINES_RUNTIME else "True"
        out.append(f"(not {term})" if negate else term)
    expr = "".join(out)
    if not any(v in expr for v in YAMBO_DEFINES_RUNTIME.values()):
        return str(eval(expr))                       # only constants: known now
    return expr


_FORTRAN_OPS = [(r"\.and\.", " and "), (r"\.or\.", " or "), (r"\.not\.", " not "),
                (r"\.true\.", " True "), (r"\.false\.", " False "),
                (r"\.eqv\.", " == "), (r"\.neqv\.", " != ")]


def fortran_condition(c):
    """A Fortran logical expression in the conditions' small language (see
    yambo_runlevels.evaluate): and, or, not, ==, !=, <, >, any(...), all(...),
    has(text, letters), match(text, word), same(text, word)."""
    c = c.strip()
    c = re.sub(r"any\s*\(\s*\(/(.*?)/\)\s*\)", r"any_(\1)", c, flags=re.I | re.S)
    c = re.sub(r"all\s*\(\s*\(/(.*?)/\)\s*\)", r"all_(\1)", c, flags=re.I | re.S)
    c = re.sub(r"index\s*\(\s*(\w+)\s*,\s*('[^']*'|\"[^\"]*\")\s*\)\s*/=\s*0", r"has(\1, \2)", c, flags=re.I)
    c = re.sub(r"index\s*\(\s*(\w+)\s*,\s*('[^']*'|\"[^\"]*\")\s*\)\s*>\s*0", r"has(\1, \2)", c, flags=re.I)
    c = re.sub(r"STRING_match\s*\(\s*(?:trim\s*\(\s*)?(\w+)\s*\)?\s*,", r"match(\1,", c, flags=re.I)
    c = re.sub(r"STRING_same\s*\(\s*(?:trim\s*\(\s*)?(\w+)\s*\)?\s*,", r"same(\1,", c, flags=re.I)
    c = re.sub(r"trim\s*\(\s*(\w+)\s*\)", r"\1", c, flags=re.I)
    # A database's I/O status: io_X_err(2) == 0 means it was found on disk
    c = re.sub(r"\b(io_\w+)\s*\(\s*(\d+)\s*\)", r"\1_\2", c)
    for pattern, repl in _FORTRAN_OPS:
        c = re.sub(pattern, repl, c, flags=re.I)
    c = c.replace("/=", "!=")
    c = re.sub(r"(?<![=!<>])=(?!=)", "==", c)
    c = re.sub(r"(\d)\.(?!\d)", r"\1.0", c)                 # 0. -> 0.0
    c = re.sub(r"'([^']*)'", r'"\1"', c)
    return " ".join(c.split())


def _statements(text):
    """Fortran statements, comments removed and continuations joined, with the
    preprocessor lines kept as they are ('!' there is a negation, and '&&' no
    continuation)."""
    protected = "\n".join(line.replace("!", "\x01").replace("&", "\x02") if line.lstrip().startswith("#")
                          else line for line in text.splitlines())
    for line in strip_comments(protected).splitlines():
        line = line.strip().replace("\x01", "!").replace("\x02", "&")
        if line:
            yield line


def _translate(lines, what_names=None):
    """Rules from a block of yambo's Fortran: (condition, action, argument).
    Actions: activate / deactivate (names), cpu (a section's parallel
    variables), x (a response function's names: kind, stems), qp (a QP
    control section), set (a variable to an expression or text)."""
    rules, stack, ignored = [], [], []
    def cond():
        terms = [c for c in stack if c != "True"]
        return " and ".join(f"({c})" for c in terms) if terms else "True"
    for line in lines:
        if line.startswith("#"):
            m = re.match(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b\s*(.*)", line)
            if not m:
                continue
            kind, rest = m.groups()
            if kind in ("if", "ifdef", "ifndef"):
                c = (_preprocessor(rest) if kind == "if" else
                     _preprocessor(("" if kind == "ifdef" else "!") + f"defined {rest}"))
                stack.append(("pp", c, c))
            elif kind == "elif":
                _, first, _ = stack.pop()
                c = _preprocessor(rest)
                stack.append(("pp", f"not ({first}) and ({c})", f"({first}) or ({c})"))
            elif kind == "else":
                _, first, _ = stack.pop()
                stack.append(("pp", f"not ({first})", "True"))
            elif kind == "endif":
                stack.pop()
            continue
        low = line.lower()
        m = re.match(r"(?:else\s*if)\s*\((.*)\)\s*then$", line, re.I)
        if m:
            _, first, seen = stack.pop()
            c = fortran_condition(m.group(1))
            stack.append(("if", f"not ({seen}) and ({c})", f"({seen}) or ({c})"))
            continue
        if re.fullmatch(r"else", low):
            _, first, seen = stack.pop()
            stack.append(("if", f"not ({seen})", "True"))
            continue
        if re.fullmatch(r"end\s*if", low):
            stack.pop()
            continue
        m = re.match(r"if\s*\((.*)\)\s*then$", line, re.I)
        if m:
            c = fortran_condition(m.group(1))
            stack.append(("if", c, c))
            continue
        single = None
        m = re.match(r"if\s*\(", line, re.I)
        if m:                                       # if (cond) statement
            depth, i = 0, m.end() - 1
            for i in range(m.end() - 1, len(line)):
                depth += {"(": 1, ")": -1}.get(line[i], 0)
                if depth == 0:
                    break
            single, line = fortran_condition(line[m.end():i]), line[i + 1:].strip()
        conds = [c for _, c, _ in stack] + ([single] if single else [])
        if "False" in conds:
            continue
        condition = " and ".join(f"({c})" for c in conds if c != "True") or "True"
        if any(c == "False" for c in conds) or "(False)" in condition.split(" and "):
            continue
        rule = _statement(line, what_names)
        if rule is None:
            ignored.append(line)
        elif rule:
            rules.append([condition, *rule])
    return rules, ignored


def _names(arg):
    """The names of an initactivate argument: '...' strings, and what//'...'."""
    parts = re.findall(r"(what\s*//\s*)?'([^']*)'|(what\s*//\s*)?\"([^\"]*)\"", arg)
    names = []
    for w1, a, w2, b in parts:
        text = a or b
        prefix = "{what}" if (w1 or w2) else ""
        words = text.split()
        if words:
            names += [prefix + words[0]] + words[1:]
    return names


def _statement(line, what_names):
    m = re.match(r"call\s+initactivate\s*\(\s*(-?\d+)\s*,(.*)\)\s*$", line, re.I)
    if m:
        mode, names = int(m.group(1)), _names(m.group(2))
        return ["deactivate" if mode < 0 else "activate", names]
    m = re.match(r"call\s+CPU_activate\s*\(\s*[\"'](\w+)[\"']\s*\)", line, re.I)
    if m:
        return ["cpu", m.group(1)]
    m = re.match(r"call\s+X_activate\s*\(\s*(\w+|[\"']\w+[\"'])\s*,\s*\(/(.*)/\)\s*\)", line, re.I)
    if m:
        kind = m.group(1).strip("'\"")
        return ["x", [kind, re.findall(r"'(\w+)'", m.group(2))]]
    m = re.match(r"call\s+INIT_QP_ctl_switch\s*\(\s*'(\w)'\s*\)", line, re.I)
    if m:
        return ["qp", m.group(1)]
    m = re.match(r"(X_kind)\s*=\s*\"(\w+)\"", line)
    if m:
        return ["set", [m.group(1), repr(m.group(2))]]
    m = re.match(r"(l_\w+)\s*=\s*runlevel_is_on\s*\(\s*'(\w+)'\s*\)", line, re.I)
    if m:
        return ["set", [m.group(1), f"runlevel({m.group(2)!r})"]]
    m = re.match(r"(l_\w+)\s*=\s*(.*)$", line, re.I)
    if m:
        return ["set", [m.group(1), fortran_condition(m.group(2))]]
    if line == "()" or re.match(r"(subroutine|end\s+subroutine|use |implicit|integer|logical|character|do |enddo|end\s*do|contains)", line, re.I):
        return []                                   # declarations and the like
    return None


def _response_info(load, tag):
    """The response function's variables (INIT_load.F's Xload): 18 stems, each
    with a description, unit and verbosity, named and described per kind (Xx,
    Xs, Xd, Xp, Xm)."""
    text = re.sub(r"&\s*\n\s*&?", " ", load)
    stems = re.findall(r"'(\w+)'", re.search(r"nms=\(/(.*?)/\)", text, re.S).group(1))
    descriptions = {int(i): d for i, d in re.findall(r"des\((\d+)\)\s*=\s*'([^']*)'", text)}
    calls = {int(i): rest for i, rest in re.findall(r"call it\(defs,nms\(\s*(\d+)\),des\(\s*\d+\),(.*)\)", text)}
    # QpntsR and DmRnge are verbose but for Xd and Xm (V_iq, V_DmRnge: the
    # condition after the loop, `.not.X%whoami==3.and.(.not.X%whoami==5...)`)
    if "V_iq     = V_resp" not in load:
        print(f"{tag}: the verbosity of QpntsR*/DmRnge* changed in Xload: check _response_info",
              file=sys.stderr)
    info = {}
    for i, stem in enumerate(stems, start=1):
        rest = calls.get(i, "")
        unit = re.search(r"\b(\w+)_unit\b", rest)
        verb = re.search(r"verb_level\s*=\s*(\w+)", rest)
        for kind in ("Xx", "Xs", "Xd", "Xp", "Xm"):
            entry = {"text": f"[{kind}] {' '.join(descriptions.get(i, '').split())}"}
            if unit:
                entry["unit"] = unit.group(1)
            level = verb.group(1) if verb else "0"
            if level in ("V_iq", "V_DmRnge"):
                level = "0" if kind in ("Xd", "Xm") else "V_resp"
            if level != "0":
                entry["verbosity"] = level.removeprefix("V_")
            info[stem[:6] + kind] = entry
    return info


def _fortran_string(expr, arrays, index):
    """A Fortran string expression of literals and array elements joined by //
    ('[EXTQP '//trim(description_ch(ID))//'] ...'), with the indices given."""
    out = ""
    for part in re.split(r"\s*//\s*", expr.strip()):
        m = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", part)
        if m:
            out += m.group(1) if m.group(1) is not None else m.group(2)
            continue
        m = re.fullmatch(r"(trim\s*\(\s*)?(\w+)\s*\(\s*(\w+)\s*\)\s*\)?", part)
        if not m or m.group(2) not in arrays:
            raise ValueError(f"string expression {expr!r}: {part!r} not understood")
        value = arrays[m.group(2)][index[m.group(3)] - 1]
        out += value.strip() if m.group(1) else value
    return out


def _qp_ctl_info(text):
    """INIT_QP_ctl_load.F's names (prefix X, K, G; spins '', _up, _dn), with
    their descriptions and verbosity."""
    flat = strip_comments(text)
    arrays = {}
    for name, values in re.findall(r"(\w+)\s*=\s*\(/(.*?)/\)", flat):
        arrays[name] = re.findall(r"'([^']*)'", values)
    for name, i, value in re.findall(r"(\w+)\((\d+)\)\s*=\s*'([^']*)'", flat):
        arrays.setdefault(name, ["", "", ""])
        arrays[name][int(i) - 1] = value
    levels = dict(re.findall(r"(\w+)\s*=\s*V_(\w+)", flat))
    calls = [call_args(m.group(1)) for m in re.finditer(r"call\s+it\s*\((.*)", flat, re.I)]
    in_loop = re.search(r"do\s+i_f\s*=\s*1\s*,\s*3(.*?)enddo", flat, re.S | re.I)
    loop_text = in_loop.group(1) if in_loop else ""
    info = {}
    for args in calls:
        if len(args) < 3:
            continue
        name_expr, text_expr = args[1], args[2]
        verb = next((re.sub(r"verb_level\s*=\s*", "", a) for a in args if a.startswith("verb_level")), "")
        verb = levels.get(verb, verb.removeprefix("V_"))
        unit = next((re.fullmatch(r"(\w+)_unit", a).group(1) for a in args[3:]
                     if re.fullmatch(r"\w+_unit", a)), None)
        spins = range(1, 4) if name_expr in loop_text else [1]
        for ID in range(1, 4):
            for i_f in spins:
                index = {"ID": ID, "i_f": i_f}
                try:
                    name = _fortran_string(name_expr, arrays, index)
                    text_ = _fortran_string(text_expr, arrays, index)
                except (ValueError, IndexError, KeyError):
                    continue
                entry = {"text": " ".join(text_.split())}
                if verb:
                    entry["verbosity"] = verb
                if unit:
                    entry["unit"] = unit
                info[name] = entry
    return info


def _parallel_info(load, sections):
    """The parallel variables of each section (INIT_load.F's loop over
    CPU_structure), with their descriptions and verbosity."""
    fields = {int(i): f for i, f in re.findall(r"if\s*\(i_s==(\d+)\)\s*possible_fields='([^']*)'", load)}
    texts = dict(re.findall(r'name=trim\(CPU_structure\(i_s\)%Short_Description\)//"(_\w+)"\s*'
                            r'call it\(defs,trim\(name\),\s*\'([^\']*)\'',
                            re.sub(r"&\s*\n\s*&?", " ", load)))
    info = {}
    for i, section in enumerate(sections, start=1):
        for suffix in ("_CPU", "_ROLEs", "_nCPU_LinAlg_INV", "_nCPU_LinAlg_DIAGO"):
            text_ = texts.get(suffix, "[PARALLEL]")
            if suffix == "_ROLEs":
                text_ = f"{text_} {fields.get(i, '').strip()}".strip()
            info[section + suffix] = {"text": text_, "verbosity": "parallel"}
    return info


def _routine(text, *names):
    """A subroutine's body, by any of its names (they changed between versions)."""
    for name in names:
        m = re.search(rf"subroutine\s+{name}\b(.*?)end\s+subroutine", text, re.I | re.S)
        if m:
            return m.group(1)
    sys.exit(f"subroutine {' or '.join(names)} not found")


def yambo_activation(repo, tag):
    """What yambo puts in its input for given runlevels: the rules of
    INIT_activate.F, INIT.F's runlevels -> switches and the runlevels a
    runlevel brings, INIT_QP_ctl_switch.F's names, and each name's description,
    Fortran variable and verbosity level from INIT_load.F."""
    activate = show(repo, tag, "src/interface/INIT_activate.F")
    init = show(repo, tag, "src/interface/INIT.F")
    body = re.sub(r"\bcontains\b.*", "", _routine(activate, "INIT_activate"), flags=re.S | re.I)
    rules, ignored = _translate(_statements(body))
    switches, ign2 = _translate(_statements(_routine(init, "LOCAL_from_runlevels_to_logicals", "logicalson")))
    implied, ign3 = _translate(_statements(_routine(init, "LOCAL_setup_before_input_IO", "before_infile_write_setup")))
    implied = [r for r in implied if r[1] == "activate"]
    after, _ = _translate(_statements(_routine(init, "LOCAL_after_input_IO", "after_infile_write_setup",
                                               "varsetup2")))
    after = [r for r in after if r[1] == "activate"]
    cpu = _routine(activate, "CPU_activate")
    cpu_rules, _ = _translate(_statements(cpu))
    qp_rules, _ = _translate(_statements(_routine(show(repo, tag, "src/interface/INIT_QP_ctl_switch.F"),
                                                  "INIT_QP_ctl_switch")))
    qp_rules = [[re.sub(r"n_sp_pol\s*==\s*1", "not SPIN_POLARIZED", c), a, x] for c, a, x in qp_rules]
    info = {}
    for path in YAMBO_FILES:
        for m in re.finditer(r"call\s+it\s*\((.*)", strip_comments(show(repo, tag, path)), re.I):
            a = call_args(m.group(1))
            if len(a) < 3:
                continue
            flag = re.fullmatch(r"'[rf]'", a[0])
            name, desc = (a[2], a[3] if len(a) > 3 else "''") if flag else (a[1], a[2])
            lit = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", name)
            if not lit:
                continue
            name = (lit.group(1) or lit.group(2)).strip()
            d = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", desc.strip())
            entry = {"text": " ".join((d.group(1) or d.group(2) or "").split()) if d else ""}
            verb = next((re.sub(r"verb_level\s*=\s*", "", x) for x in a if x.startswith("verb_level")), "")
            verb = verb.removeprefix("V_")
            if verb and verb != "0":                 # verb_level=0: written by default
                entry["verbosity"] = verb
            if not flag and len(a) > 3 and re.fullmatch(r"[\w%]+(\([^)]*\))?", a[3]):
                entry["fortran"] = a[3]
            unit = next((m.group(1) for x in a[3:]
                         for m in [re.fullmatch(r"(?:unit\s*=\s*)?(\w+)_unit", x)] if m), None)
            if unit:
                entry["unit"] = unit
            info[name] = entry
    load = show(repo, tag, "src/interface/INIT_load.F")
    info.update(_response_info(load, tag))
    info.update(_qp_ctl_info(show(repo, tag, "src/interface/INIT_QP_ctl_load.F")))
    sections = re.findall(r'Short_Description="(\w+)"', show(repo, tag, "src/modules/mod_parallel.F"))
    info.update(_parallel_info(load, sections))
    # QPkrange and QPerange: written by INIT_QP (not activated), when INIT.F's
    # QP_field holds; QPerange only at the "qp" verbosity
    qp_field = re.search(r"QP_field\s*=\s*(.*)", "\n".join(_statements(init)))
    if qp_field:
        rules.append([fortran_condition(qp_field.group(1)), "activate", ["QPkrange", "QPerange"]])
        info["QPkrange"] = {"text": "[GW] QP generalized Kpoint/Band indices"}
        info["QPerange"] = {"text": "[GW] QP generalized Kpoint/Energy indices", "verbosity": "qp"}
    else:
        print(f"{tag}: QP_field not found in INIT.F: QPkrange is not in the rules", file=sys.stderr)
    # QptCoord: written by INIT_q_points, called by the setup run while the
    # SAVE has no ndb.kindx (QINDX); for plain yambo only at the "kpt" verbosity
    q_points = re.search(r"if\s*\((.*)\)\s*call\s+INIT_q_points", "\n".join(_statements(init)), re.I)
    if q_points:
        rules.append([fortran_condition(q_points.group(1)), "activate", ["QptCoord"]])
        info["QptCoord"] = {"text": "[KPT] [iku] Q-points coordinates (compatibility)",
                            "verbosity": "kpt"}
    unknown = [x for x in ignored if not x.lower().startswith(("call init_rt_ctl_switch", "call afield"))]
    if unknown:
        print(f"{tag}: statements of INIT_activate not translated: {unknown}", file=sys.stderr)
    return {"rules": rules, "switches": switches, "implied": implied, "after": after, "cpu": cpu_rules,
            "qp": qp_rules, "info": {k: info[k] for k in sorted(info)}}


def ypp_names(repo, tag):
    """ypp's names: its loader's (INIT_load_ypp.F: runlevels, flags, variables,
    each name literal), the QP controls it loads (yambo's, prefix G), the
    names read with parser()/PARSER_array in ypp's sources, and the runlevels
    of its options (options_ypp.c); the units its variables take."""
    text = strip_comments(show(repo, tag, "ypp/interface/INIT_load_ypp.F"))
    runlevels, flags, variables, units = set(), set(), set(), {}
    for m in re.finditer(r"call\s+it\s*\((.*)", text, re.I):
        a = call_args(m.group(1))
        if len(a) < 2:
            continue
        kind = {"'r'": "r", "'f'": "f"}.get(a[0], "v")
        name = a[2] if kind != "v" else a[1]
        lit = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", name)
        if not lit:
            sys.exit(f"{tag}: ypp builds a name this script does not know: {name}")
        name = (lit.group(1) or lit.group(2)).strip()
        {"r": runlevels, "f": flags, "v": variables}[kind].add(name)
        unit = next((m2.group(1) for x in a[3:] for m2 in [re.fullmatch(r"(?:unit\s*=\s*)?(\w+_unit)", x)] if m2),
                    None)
        if unit:
            units[name] = unit
    if re.search(r"call\s+INIT_QP_ctl_load\s*\(\s*defs\s*,\s*3\s*\)", text, re.I):
        variables |= {n for n in _qp_ctl_info(show(repo, tag, "src/interface/INIT_QP_ctl_load.F"))
                      if n.startswith("G")}
    for options in ("src/driver/options_ypp.c", "src/driver/options_control.c"):   # its own, and the shared ones
        runlevels |= set(re.findall(r'yambo_string="([^"]+)"', show(repo, tag, options))) - {"undef"}
    grep = subprocess.run(["git", "-C", repo, "grep", "-hoE",
                           r"""PARSER_array\([^)]*VAR=["'][A-Za-z_0-9]+["']|call +parser\( *["'][A-Za-z_0-9]+["']""",
                           tag, "--", "ypp/*"], capture_output=True, text=True).stdout
    for name in re.findall(r"""["']([A-Za-z_0-9]+)["']\s*$""", grep, re.M):
        if name not in runlevels and name not in flags:
            variables.add(name)
    return {"runlevels": sorted(runlevels), "flags": sorted(flags), "variables": sorted(variables),
            "patterns": [], "units": {k: units[k] for k in sorted(units)}}


def write_data(module, name, data):
    text = module.read_text()
    start = text.index(f"{name} = ")
    end = text.index("\n\n\n", start)        # the data ends at the first gap
    module.write_text(text[:start] + f"{name} = " + pprint.pformat(data, width=100, compact=True)
                      + text[end:])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("qe_repo")
    ap.add_argument("yambo_repo")
    ap.add_argument("--qe", nargs="+", default=["qe-7.3.1", "qe-7.5", "qe-7.6"])
    ap.add_argument("--yambo", nargs="+", default=["5.1.1", "5.3.0", "5.4-beta"])
    args = ap.parse_args()
    write_data(QE_MODULE, "PW_NAMELISTS",
               {t.removeprefix("qe-"): qe_namelists(args.qe_repo, t) for t in args.qe})
    write_data(YAMBO_MODULE, "YAMBO_NAMES",
               {re.sub(r"-.*$", "", t): yambo_names(args.yambo_repo, t) for t in args.yambo})
    write_data(YAMBO_MODULE, "YPP_NAMES",
               {re.sub(r"-.*$", "", t): ypp_names(args.yambo_repo, t) for t in args.yambo})
    write_data(YAMBO_MODULE, "YAMBO_OPTIONS",
               {re.sub(r"-.*$", "", t): yambo_options(args.yambo_repo, t) for t in args.yambo})
    write_data(RUNLEVELS_MODULE, "YAMBO_RUNLEVELS",
               {re.sub(r"-.*$", "", t): yambo_activation(args.yambo_repo, t) for t in args.yambo})
    print(f"Written {QE_MODULE.relative_to(ROOT)}, {YAMBO_MODULE.relative_to(ROOT)} and "
          f"{RUNLEVELS_MODULE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
